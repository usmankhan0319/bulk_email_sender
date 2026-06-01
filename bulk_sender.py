"""Bulk email sender with SMTP rotation, rate limiting, retries and open tracking.

Usage:
    python bulk_sender.py --import recipients.csv content.csv
    python bulk_sender.py --run        # continuous send loop
    python bulk_sender.py --run-once   # ek hi batch bhej ke exit
    python bulk_sender.py --stats      # quick status snapshot
"""

import argparse
import csv
import logging
import os
import random
import smtplib
import ssl
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import Template

import db

load_dotenv()

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "sender.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("bulk_sender")


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------

TRACKING_BASE_URL = os.getenv("TRACKING_BASE_URL", "").rstrip("/")
DAILY_GLOBAL_LIMIT = int(os.getenv("DAILY_GLOBAL_LIMIT", "5000"))
PER_SMTP_MIN_DELAY = float(os.getenv("PER_SMTP_MIN_DELAY", "3"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "5"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))
DEFAULT_REPLY_TO = os.getenv("DEFAULT_REPLY_TO", "")
UNSUBSCRIBE_URL = os.getenv("UNSUBSCRIBE_URL", "")


def load_smtp_configs():
    configs = []
    i = 1
    while True:
        host = os.getenv(f"SMTP_{i}_HOST")
        if not host:
            break
        configs.append(
            {
                "key": f"smtp_{i}",
                "host": host,
                "port": int(os.getenv(f"SMTP_{i}_PORT", "587")),
                "user": os.getenv(f"SMTP_{i}_USER", ""),
                "password": os.getenv(f"SMTP_{i}_PASS", ""),
                "from_name": os.getenv(f"SMTP_{i}_FROM_NAME", ""),
                "from_email": os.getenv(
                    f"SMTP_{i}_FROM_EMAIL", os.getenv(f"SMTP_{i}_USER", "")
                ),
                "daily_limit": int(os.getenv(f"SMTP_{i}_DAILY_LIMIT", "1000")),
                "use_tls": os.getenv(f"SMTP_{i}_USE_TLS", "true").lower() == "true",
                "_last_sent": 0.0,
                "_lock": threading.Lock(),
            }
        )
        i += 1
    if not configs:
        raise SystemExit("No SMTP servers configured. Check your .env file.")
    log.info("Loaded %d SMTP server(s)", len(configs))
    return configs


# ----------------------------------------------------------------------
# CSV import
# ----------------------------------------------------------------------

def import_csvs(recipients_path, content_path):
    db.init_db()

    contents = {}
    with open(content_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            contents[row["content_id"]] = row
    log.info("Loaded %d content template(s)", len(contents))

    added = 0
    with open(recipients_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            email = (row.get("email") or "").strip().lower()
            if not email or "@" not in email:
                continue
            content_id = (row.get("content_id") or "").strip()
            if content_id not in contents:
                log.warning("Skipping %s: content_id %r missing", email, content_id)
                continue
            db.upsert_recipient(
                email=email,
                first_name=(row.get("first_name") or "").strip(),
                last_name=(row.get("last_name") or "").strip(),
                company=(row.get("company") or "").strip(),
                content_id=content_id,
                tracking_id=uuid.uuid4().hex,
            )
            added += 1
    log.info("Imported %d recipients into DB", added)
    return contents


def load_contents():
    content_path = Path(__file__).parent / "content.csv"
    contents = {}
    with open(content_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            contents[row["content_id"]] = row
    return contents


# ----------------------------------------------------------------------
# Email building
# ----------------------------------------------------------------------

def render(template_str, ctx):
    return Template(template_str).render(**ctx)


def build_message(smtp_cfg, recipient, content_row):
    ctx = {
        "first_name": recipient.get("first_name") or "there",
        "last_name": recipient.get("last_name") or "",
        "company": recipient.get("company") or "",
        "email": recipient["email"],
    }

    subject = render(content_row["subject"], ctx)
    html_body = render(content_row["html_body"], ctx)
    text_body = render(content_row["text_body"], ctx)

    tracking_id = recipient["tracking_id"]
    if TRACKING_BASE_URL:
        pixel = (
            f'<img src="{TRACKING_BASE_URL}/track/open/{tracking_id}.png" '
            f'width="1" height="1" alt="" style="display:none" />'
        )
        html_body = html_body + pixel

    if UNSUBSCRIBE_URL:
        unsub_html = (
            f'<p style="font-size:11px;color:#888;margin-top:24px">'
            f'If you no longer wish to receive these emails, '
            f'<a href="{UNSUBSCRIBE_URL}?e={recipient["email"]}">unsubscribe here</a>.</p>'
        )
        html_body += unsub_html
        text_body += f"\n\nUnsubscribe: {UNSUBSCRIBE_URL}?e={recipient['email']}"

    msg = MIMEMultipart("alternative")
    from_name = smtp_cfg["from_name"] or smtp_cfg["from_email"]
    msg["From"] = f"{from_name} <{smtp_cfg['from_email']}>"
    msg["To"] = recipient["email"]
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=smtp_cfg["from_email"].split("@")[-1])
    if DEFAULT_REPLY_TO:
        msg["Reply-To"] = DEFAULT_REPLY_TO
    if UNSUBSCRIBE_URL:
        msg["List-Unsubscribe"] = f"<{UNSUBSCRIBE_URL}?e={recipient['email']}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg["X-Mailer"] = "Neurocare-BulkSender/1.0"

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


# ----------------------------------------------------------------------
# SMTP sending
# ----------------------------------------------------------------------

def send_via_smtp(smtp_cfg, msg, to_email):
    context = ssl.create_default_context()
    if smtp_cfg["port"] == 465:
        server = smtplib.SMTP_SSL(smtp_cfg["host"], smtp_cfg["port"], timeout=30, context=context)
    else:
        server = smtplib.SMTP(smtp_cfg["host"], smtp_cfg["port"], timeout=30)
        server.ehlo()
        if smtp_cfg["use_tls"]:
            server.starttls(context=context)
            server.ehlo()
    try:
        if smtp_cfg["user"]:
            server.login(smtp_cfg["user"], smtp_cfg["password"])
        server.sendmail(smtp_cfg["from_email"], [to_email], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass


def pick_smtp(smtps):
    """Round-robin-ish picker that respects per-SMTP daily limits and min delay."""
    candidates = []
    now = time.time()
    for s in smtps:
        sent = db.get_smtp_sent_today(s["key"])
        if sent >= s["daily_limit"]:
            continue
        cooldown_left = (s["_last_sent"] + PER_SMTP_MIN_DELAY) - now
        candidates.append((cooldown_left, sent, s))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: (x[0], x[1]))
    cooldown_left, _, chosen = candidates[0]
    return chosen, max(0.0, cooldown_left)


def send_one(smtps, recipient, contents):
    smtp_cfg, wait = pick_smtp(smtps)
    if smtp_cfg is None:
        return False, "no_smtp_available"
    if wait > 0:
        time.sleep(wait + random.uniform(0, 0.4))

    content_row = contents.get(recipient["content_id"])
    if not content_row:
        db.mark_failed(recipient["id"], "n/a", f"missing content_id {recipient['content_id']}", False)
        return False, "missing_content"

    msg = build_message(smtp_cfg, recipient, content_row)
    try:
        with smtp_cfg["_lock"]:
            send_via_smtp(smtp_cfg, msg, recipient["email"])
            smtp_cfg["_last_sent"] = time.time()
        db.mark_sent(recipient["id"], smtp_cfg["key"])
        log.info("SENT  %-35s via %s", recipient["email"], smtp_cfg["key"])
        return True, None
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as e:
        db.mark_failed(recipient["id"], smtp_cfg["key"], e, retryable=False)
        log.warning("HARD-FAIL %s via %s: %s", recipient["email"], smtp_cfg["key"], e)
        return False, "hard_fail"
    except Exception as e:
        db.mark_failed(recipient["id"], smtp_cfg["key"], e, retryable=True)
        log.warning("SOFT-FAIL %s via %s: %s", recipient["email"], smtp_cfg["key"], e)
        return False, "soft_fail"


# ----------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------

def run_loop(once=False):
    db.init_db()
    smtps = load_smtp_configs()
    contents = load_contents()

    log.info("Starting send loop. Global daily limit: %d. Workers: %d. Batch: %d",
             DAILY_GLOBAL_LIMIT, MAX_WORKERS, BATCH_SIZE)

    while True:
        sent_today = db.get_global_sent_today()
        if sent_today >= DAILY_GLOBAL_LIMIT:
            log.info("Daily global limit reached (%d). Sleeping 5 minutes.", sent_today)
            if once:
                return
            time.sleep(300)
            continue

        remaining_quota = DAILY_GLOBAL_LIMIT - sent_today
        batch = db.fetch_pending(min(BATCH_SIZE, remaining_quota))
        if not batch:
            log.info("Nothing pending. Sleeping 60s. (sent today: %d)", sent_today)
            if once:
                return
            time.sleep(60)
            continue

        log.info("Processing batch of %d (sent today so far: %d)", len(batch), sent_today)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = [ex.submit(send_one, smtps, r, contents) for r in batch]
            for _ in as_completed(futures):
                pass

        if once:
            return


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def cli():
    parser = argparse.ArgumentParser(description="Bulk email sender")
    parser.add_argument(
        "--import",
        dest="import_files",
        nargs=2,
        metavar=("RECIPIENTS_CSV", "CONTENT_CSV"),
        help="Import recipients + content CSV into the DB",
    )
    parser.add_argument("--run", action="store_true", help="Run continuous send loop")
    parser.add_argument("--run-once", action="store_true", help="Process one batch and exit")
    parser.add_argument("--stats", action="store_true", help="Show stats and exit")
    args = parser.parse_args()

    if args.import_files:
        import_csvs(*args.import_files)
        return
    if args.stats:
        db.init_db()
        s = db.stats()
        print("Recipient status counts:")
        for k, v in s.items():
            print(f"  {k:<10} {v}")
        print(f"Sent today (UTC): {db.get_global_sent_today()}")
        return
    if args.run:
        run_loop(once=False)
        return
    if args.run_once:
        run_loop(once=True)
        return
    parser.print_help()


if __name__ == "__main__":
    cli()
