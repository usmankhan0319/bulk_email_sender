"""FastAPI app for the Bulk Email Sender dashboard.

Routes:
  GET  /                         -> dashboard HTML
  GET  /api/stats                -> overall counters
  GET  /api/logs?after=ID        -> live activity log tail
  GET  /api/stats/domains        -> per-domain stats
  GET  /api/stats/smtps          -> per-SMTP stats

  GET    /api/smtp               -> list SMTP servers
  POST   /api/smtp               -> add SMTP server (auto-tests, activates on success)
  POST   /api/smtp/{id}/test     -> re-test a server
  DELETE /api/smtp/{id}          -> remove

  POST /api/upload/content       -> upload docx/html/txt; returns {subject, html_body, text_body}
  POST /api/upload/recipients    -> upload xlsx/csv; returns parsed list + invalid rows

  POST /api/spam-check           -> body: {subject, html_body, text_body} -> spam report
  POST /api/campaigns            -> create + (optionally) start or schedule
  GET  /api/campaigns            -> list
  GET  /api/campaigns/{id}       -> detail
  POST /api/campaigns/{id}/start -> start now
  POST /api/campaigns/{id}/pause -> pause
  POST /api/campaigns/{id}/resume -> resume
  GET  /api/campaigns/{id}/recipients -> recipient page

  GET  /track/open/{tid}.png     -> 1x1 pixel + log open
  GET  /healthz                  -> liveness
"""

import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import db
from services import deliverability, parsers, smtp_ops, spam_check
from worker import get_worker

load_dotenv()

BASE_DIR = Path(__file__).parent
PIXEL_PATH = BASE_DIR / "pixel.png"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


def _ensure_pixel():
    if PIXEL_PATH.exists():
        return
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfc\xff"
        b"\xff?\x03\x00\x05\xfe\x02\xfe\xa3I[\xe6\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    PIXEL_PATH.write_bytes(png)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    _ensure_pixel()
    get_worker().start()
    db.applog("info", "App started", source="app")
    yield
    get_worker().stop()


app = FastAPI(title="Bulk Email Sender", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ---------- pydantic models ----------

class SmtpIn(BaseModel):
    name: str
    host: str
    port: int = 587
    username: str
    password: str
    from_email: str
    from_name: str = ""
    daily_limit: int = 500
    use_tls: bool = True


class SpamCheckIn(BaseModel):
    subject: str
    html_body: str
    text_body: str = ""


class CampaignIn(BaseModel):
    name: str
    subject: str
    html_body: str
    text_body: str
    recipients: list[dict] = Field(default_factory=list)
    action: str = "save"           # save | start_now | schedule
    scheduled_at: Optional[str] = None  # ISO datetime in UTC


# ---------- dashboard ----------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/healthz")
async def healthz():
    return {"ok": True}


# ---------- stats / logs ----------

@app.get("/api/stats")
async def api_stats():
    return db.overall_stats()


@app.get("/api/stats/domains")
async def api_stats_domains():
    return {"domains": db.per_domain_stats()}


@app.get("/api/stats/smtps")
async def api_stats_smtps():
    return {"smtps": db.per_smtp_stats()}


@app.get("/api/logs")
async def api_logs(after: int = 0, campaign_id: Optional[int] = None, limit: int = 200):
    return {"logs": db.get_logs(after_id=after, campaign_id=campaign_id, limit=limit)}


# ---------- SMTP ----------

@app.get("/api/smtp")
async def api_smtp_list():
    rows = db.list_smtps()
    for r in rows:
        r.pop("password", None)
    return {"smtps": rows}


@app.post("/api/smtp")
async def api_smtp_add(payload: SmtpIn):
    data = payload.model_dump()
    smtp_id = db.add_smtp(data)
    db.applog("info", f"SMTP added: {data['name']} ({data['host']}). Testing connection...",
              source="smtp")
    ehlo_name = data["from_email"].split("@")[-1] if "@" in data["from_email"] else None
    ok, err = smtp_ops.test_connection(
        data["host"], data["port"], data["username"], data["password"],
        data["use_tls"], ehlo_name=ehlo_name,
    )
    if ok:
        db.update_smtp_status(smtp_id, "active")
        db.applog("info", f"SMTP {data['name']} -> active", source="smtp")
    else:
        db.update_smtp_status(smtp_id, "inactive", err)
        db.applog("error", f"SMTP {data['name']} -> inactive: {err}", source="smtp")

    # Run domain deliverability check on the from_email domain
    delivery = None
    if "@" in data["from_email"]:
        domain = data["from_email"].split("@")[-1]
        try:
            delivery = deliverability.check_domain(domain)
            level = "info" if delivery["verdict"] == "good" else (
                "warn" if delivery["verdict"] == "warn" else "error")
            db.applog(level,
                      f"Domain {domain} deliverability: {delivery['score']}/100 ({delivery['verdict']}). "
                      f"Issues: {len(delivery['issues'])}.",
                      source="smtp")
        except Exception as e:
            db.applog("warn", f"Domain check failed for {domain}: {e}", source="smtp")
    return {"id": smtp_id, "ok": ok, "error": err, "deliverability": delivery}


@app.get("/api/deliverability")
async def api_deliverability(domain: str = Query(..., min_length=3)):
    try:
        return deliverability.check_domain(domain)
    except Exception as e:
        raise HTTPException(500, f"Lookup failed: {e}")


@app.post("/api/smtp/{smtp_id}/test")
async def api_smtp_test(smtp_id: int):
    s = db.get_smtp(smtp_id)
    if not s:
        raise HTTPException(404, "Not found")
    ehlo_name = s["from_email"].split("@")[-1] if s.get("from_email") and "@" in s["from_email"] else None
    ok, err = smtp_ops.test_connection(
        s["host"], s["port"], s["username"], s["password"], bool(s["use_tls"]),
        ehlo_name=ehlo_name,
    )
    db.update_smtp_status(smtp_id, "active" if ok else "inactive", err)
    db.applog("info" if ok else "error",
              f"SMTP {s['name']} re-tested -> {'active' if ok else 'inactive: ' + (err or '')}",
              source="smtp")
    return {"ok": ok, "error": err}


@app.delete("/api/smtp/{smtp_id}")
async def api_smtp_delete(smtp_id: int):
    s = db.get_smtp(smtp_id)
    if not s:
        raise HTTPException(404, "Not found")
    db.delete_smtp(smtp_id)
    db.applog("info", f"SMTP {s['name']} removed", source="smtp")
    return {"ok": True}


# ---------- uploads ----------

@app.post("/api/upload/content")
async def api_upload_content(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        subject, html_body, text_body = parsers.parse_content_file(file.filename, raw)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {
        "filename": file.filename,
        "subject": subject,
        "html_body": html_body,
        "text_body": text_body,
    }


@app.post("/api/upload/recipients")
async def api_upload_recipients(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        recipients, invalid = parsers.parse_recipients_file(file.filename, raw)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {
        "filename": file.filename,
        "count": len(recipients),
        "invalid_count": len(invalid),
        "recipients": recipients[:5000],   # cap response size; full list goes in via /campaigns
        "invalid": invalid[:200],
    }


# ---------- spam check ----------

@app.post("/api/spam-check")
async def api_spam_check(payload: SpamCheckIn):
    unsub = os.getenv("UNSUBSCRIBE_URL", "")
    return spam_check.analyze(
        subject=payload.subject,
        html_body=payload.html_body,
        text_body=payload.text_body,
        has_unsubscribe=bool(unsub),
    )


# ---------- campaigns ----------

@app.post("/api/campaigns")
async def api_campaign_create(payload: CampaignIn):
    if not payload.recipients:
        raise HTTPException(400, "No recipients provided")

    unsub = os.getenv("UNSUBSCRIBE_URL", "")
    report = spam_check.analyze(
        subject=payload.subject,
        html_body=payload.html_body,
        text_body=payload.text_body,
        has_unsubscribe=bool(unsub),
    )

    status = "draft"
    scheduled_at = None
    if payload.action == "start_now":
        status = "running"
    elif payload.action == "schedule":
        if not payload.scheduled_at:
            raise HTTPException(400, "scheduled_at required when action=schedule")
        status = "scheduled"
        scheduled_at = payload.scheduled_at

    cid = db.create_campaign({
        "name": payload.name,
        "subject": payload.subject,
        "html_body": payload.html_body,
        "text_body": payload.text_body,
        "status": status,
        "scheduled_at": scheduled_at,
        "spam_score": report["score"],
        "spam_report": str(report["issues"]),
    })

    inserted = 0
    for r in payload.recipients:
        em = (r.get("email") or "").strip().lower()
        if not em or "@" not in em:
            continue
        ok = db.add_recipient(
            campaign_id=cid,
            email=em,
            first_name=(r.get("first_name") or "").strip(),
            last_name=(r.get("last_name") or "").strip(),
            company=(r.get("company") or "").strip(),
            tracking_id=uuid.uuid4().hex,
        )
        if ok:
            inserted += 1

    db.refresh_campaign_counts(cid)
    if status == "running":
        db.update_campaign(cid, started_at=db.now_iso())
    db.applog(
        "info",
        f"Campaign #{cid} '{payload.name}' created with {inserted} recipients (status={status})",
        source="campaign",
        campaign_id=cid,
    )
    return {"id": cid, "inserted": inserted, "status": status, "spam": report}


@app.get("/api/campaigns")
async def api_campaign_list():
    return {"campaigns": db.list_campaigns()}


@app.get("/api/campaigns/{cid}")
async def api_campaign_get(cid: int):
    c = db.get_campaign(cid)
    if not c:
        raise HTTPException(404, "Not found")
    return c


@app.post("/api/campaigns/{cid}/start")
async def api_campaign_start(cid: int):
    c = db.get_campaign(cid)
    if not c:
        raise HTTPException(404, "Not found")
    db.update_campaign(cid, status="running", started_at=db.now_iso())
    db.applog("info", f"Campaign #{cid} started manually", source="campaign", campaign_id=cid)
    return {"ok": True}


@app.post("/api/campaigns/{cid}/pause")
async def api_campaign_pause(cid: int):
    c = db.get_campaign(cid)
    if not c:
        raise HTTPException(404, "Not found")
    db.update_campaign(cid, status="paused")
    db.applog("info", f"Campaign #{cid} paused", source="campaign", campaign_id=cid)
    return {"ok": True}


@app.post("/api/campaigns/{cid}/resume")
async def api_campaign_resume(cid: int):
    c = db.get_campaign(cid)
    if not c:
        raise HTTPException(404, "Not found")
    db.update_campaign(cid, status="running")
    db.applog("info", f"Campaign #{cid} resumed", source="campaign", campaign_id=cid)
    return {"ok": True}


@app.get("/api/campaigns/{cid}/recipients")
async def api_campaign_recipients(cid: int, limit: int = 200, offset: int = 0):
    return {"recipients": db.list_recipients(cid, limit=limit, offset=offset)}


# ---------- open tracking ----------

@app.get("/track/open/{tracking_id}.png")
async def track_open(tracking_id: str, request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "")
    ua = request.headers.get("user-agent", "")
    try:
        db.record_open(tracking_id, ip, ua)
    except Exception:
        pass
    return FileResponse(
        str(PIXEL_PATH),
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


# ---------- error handler ----------

@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    db.applog("error", f"Unhandled: {request.url.path}  {exc}", source="app")
    return JSONResponse({"error": str(exc)}, status_code=500)
