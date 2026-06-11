"""Background worker thread — runs inside the Flask app process.

Responsibilities:
  - Pick up campaigns whose scheduled_at <= now and flip them to 'running'
  - For each running campaign, send pending recipients respecting per-SMTP
    daily limits and a small per-SMTP delay
  - Update counters, write activity log entries
"""

import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import db
from services import smtp_ops


class Worker:
    def __init__(self):
        self.thread = None
        self.stop_flag = threading.Event()
        self._smtp_locks = {}
        self._last_sent = {}
        self._cap_logged = {}
        self._last_campaign_send = {}
        self.PER_SMTP_MIN_DELAY = float(os.getenv("PER_SMTP_MIN_DELAY", "3"))
        self.MAX_WORKERS = int(os.getenv("MAX_WORKERS", "5"))
        self.BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))
        self.TRACKING_BASE_URL = os.getenv("TRACKING_BASE_URL", "")
        self.UNSUBSCRIBE_URL = os.getenv("UNSUBSCRIBE_URL", "")
        self.DEFAULT_REPLY_TO = os.getenv("DEFAULT_REPLY_TO", "")

    # ---------- lifecycle ----------

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="mail-worker")
        self.thread.start()
        db.applog("info", "Worker thread started", source="worker")

    def stop(self):
        self.stop_flag.set()

    # ---------- main loop ----------

    def _loop(self):
        while not self.stop_flag.is_set():
            try:
                # 1) Promote any scheduled campaigns whose time has come
                for cid in db.claim_scheduled_campaigns():
                    db.update_campaign(cid, status="running", started_at=db.now_iso())
                    db.applog("info", f"Campaign #{cid} started (scheduled time reached)",
                              source="worker", campaign_id=cid)

                # 2) Find running campaigns and process a batch each
                running = [c for c in db.list_campaigns() if c["status"] == "running"]
                if not running:
                    time.sleep(3)
                    continue

                for camp in running:
                    if self.stop_flag.is_set():
                        break
                    try:
                        self._process_campaign(camp)
                    except Exception as e:
                        # One bad campaign must not stall the others or spam the loop.
                        db.applog("error",
                                  f"Campaign #{camp['id']} processing error: {e}",
                                  source="worker", campaign_id=camp["id"])
                # small breather so a capped/idle campaign doesn't busy-loop
                time.sleep(2)
            except Exception as e:
                db.applog("error", f"Worker loop error: {e}", source="worker")
                time.sleep(5)

    # ---------- per-campaign ----------

    def _process_campaign(self, camp):
        cid = camp["id"]
        pending = db.count_pending(cid)
        if pending == 0:
            db.refresh_campaign_counts(cid)
            db.update_campaign(cid, status="completed", completed_at=db.now_iso())
            db.applog("info", f"Campaign #{cid} completed.", source="worker", campaign_id=cid)
            return

        smtps = db.list_active_smtps()
        # Per-campaign SMTP selection: if campaign picked specific servers, use only those
        selected = self._campaign_smtp_ids(camp)
        if selected:
            smtps = [s for s in smtps if s["id"] in selected]
        if not smtps:
            db.applog("error",
                      f"Campaign #{cid}: no active SMTP servers available "
                      f"({'selected set inactive' if selected else 'none configured'}) — pausing.",
                      source="worker", campaign_id=cid)
            db.update_campaign(cid, status="paused")
            return

        # Check daily capacity (warmup-aware) across the campaign's SMTPs
        capacity = sum(max(0, db.effective_daily_limit(s) - db.get_smtp_sent_today(s["id"]))
                       for s in smtps)
        if capacity <= 0:
            if not self._cap_logged.get(cid):
                warmup_on = any(s.get("warmup_enabled") for s in smtps)
                reason = "warmup daily cap" if warmup_on else "daily limit"
                db.applog("info",
                          f"Campaign #{cid}: {reason} reached for today — "
                          f"{pending} recipients will resume tomorrow.",
                          source="worker", campaign_id=cid)
                self._cap_logged[cid] = True
            return
        self._cap_logged[cid] = False

        # Send-gap (throttle): if set, send ONE email per gap-interval, non-blocking.
        gap = int(camp.get("send_gap_seconds") or 0)
        if gap > 0:
            last = self._last_campaign_send.get(cid, 0.0)
            if (time.time() - last) < gap:
                return  # not time yet; loop will revisit
            batch = db.fetch_pending_for_campaign(cid, 1)
            if not batch:
                return
            self._send_one(smtps, batch[0], camp)
            self._last_campaign_send[cid] = time.time()
            db.refresh_campaign_counts(cid)
            return

        # Normal mode: parallel batch
        batch = db.fetch_pending_for_campaign(cid, min(self.BATCH_SIZE, capacity))
        if not batch:
            return

        db.applog("info", f"Campaign #{cid}: sending batch of {len(batch)}",
                  source="worker", campaign_id=cid)

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as ex:
            futures = [ex.submit(self._send_one, smtps, r, camp) for r in batch]
            for _ in as_completed(futures):
                pass

        db.refresh_campaign_counts(cid)

    @staticmethod
    def _campaign_smtp_ids(camp):
        """Parse the campaign's smtp_ids field (comma-separated) into a set of ints."""
        raw = (camp.get("smtp_ids") or "").strip()
        if not raw:
            return None
        out = set()
        for tok in raw.replace(";", ",").split(","):
            tok = tok.strip()
            if tok.isdigit():
                out.add(int(tok))
        return out or None

    # ---------- send one ----------

    def _pick_smtp(self, smtps):
        now = time.time()
        candidates = []
        for s in smtps:
            sent = db.get_smtp_sent_today(s["id"])
            cap = db.effective_daily_limit(s)   # honors warmup ramp
            if sent >= cap:
                continue
            last = self._last_sent.get(s["id"], 0.0)
            cooldown_left = (last + self.PER_SMTP_MIN_DELAY) - now
            candidates.append((cooldown_left, sent, s))
        if not candidates:
            return None, None
        candidates.sort(key=lambda x: (x[0], x[1]))
        cd, _, chosen = candidates[0]
        return chosen, max(0.0, cd)

    def _send_one(self, smtps, recipient, camp):
        smtp_cfg, wait = self._pick_smtp(smtps)
        if smtp_cfg is None:
            return
        if wait > 0:
            time.sleep(wait + random.uniform(0.1, 0.5))

        lock = self._smtp_locks.setdefault(smtp_cfg["id"], threading.Lock())
        # Building the message (content render) must NEVER crash the worker —
        # bad placeholders etc. mark this recipient failed, not the whole loop.
        try:
            msg = smtp_ops.build_message(
                smtp_cfg, recipient,
                camp["subject"], camp["html_body"], camp["text_body"],
                self.TRACKING_BASE_URL, self.UNSUBSCRIBE_URL, self.DEFAULT_REPLY_TO,
            )
        except Exception as e:
            db.mark_failed(recipient["id"], smtp_cfg["id"], f"build error: {e}", retryable=False)
            db.applog("error",
                      f"BUILD-FAIL  {recipient['email']}: {e}",
                      source="send", campaign_id=camp["id"])
            return
        try:
            with lock:
                smtp_ops.send_message(smtp_cfg, msg, recipient["email"])
                self._last_sent[smtp_cfg["id"]] = time.time()
            db.mark_sent(recipient["id"], smtp_cfg["id"])
            db.applog("info",
                      f"SENT  {recipient['email']}  via {smtp_cfg['name']}",
                      source="send", campaign_id=camp["id"])
        except Exception as e:
            err = str(e)
            retryable = not any(s in err.lower() for s in
                                ["recipient refused", "5.1.1", "5.7.1", "blocked"])
            db.mark_failed(recipient["id"], smtp_cfg["id"], err, retryable)
            level = "warn" if retryable else "error"
            db.applog(level,
                      f"FAIL  {recipient['email']}  via {smtp_cfg['name']}: {err[:200]}",
                      source="send", campaign_id=camp["id"])


_worker_singleton = None


def get_worker():
    global _worker_singleton
    if _worker_singleton is None:
        _worker_singleton = Worker()
    return _worker_singleton
