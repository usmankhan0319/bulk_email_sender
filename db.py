"""SQLite helper for the bulk email sender web app.

Tables:
  smtp_servers   - SMTP credentials managed via the UI
  campaigns      - each upload + send job
  recipients     - per-campaign recipient list
  send_log       - raw send attempts
  open_log       - email open events (tracking pixel)
  app_log        - free-form activity log (shown live in UI)
  smtp_counters  - per-SMTP daily counters
"""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "mailer.db"
_LOCK = threading.Lock()


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_conn():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS smtp_servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                from_email TEXT NOT NULL,
                from_name TEXT,
                daily_limit INTEGER NOT NULL DEFAULT 500,
                use_tls INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'inactive',
                last_tested_at TEXT,
                last_test_error TEXT,
                warmup_enabled INTEGER NOT NULL DEFAULT 0,
                warmup_start TEXT,
                reply_to TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                subject TEXT NOT NULL,
                html_body TEXT NOT NULL,
                text_body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                scheduled_at TEXT,
                spam_score INTEGER,
                spam_report TEXT,
                smtp_ids TEXT,
                send_gap_seconds INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                sent INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                opened INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                email TEXT NOT NULL,
                first_name TEXT,
                last_name TEXT,
                company TEXT,
                tracking_id TEXT UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                smtp_used TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                sent_at TEXT,
                opened_at TEXT,
                open_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_recipients_campaign ON recipients(campaign_id);
            CREATE INDEX IF NOT EXISTS idx_recipients_status ON recipients(status);
            CREATE INDEX IF NOT EXISTS idx_recipients_tracking ON recipients(tracking_id);
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_campaign_email ON recipients(campaign_id, email);

            CREATE TABLE IF NOT EXISTS send_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_id INTEGER NOT NULL,
                smtp_id INTEGER,
                ok INTEGER NOT NULL,
                error TEXT,
                at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS open_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracking_id TEXT NOT NULL,
                ip TEXT,
                user_agent TEXT,
                at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                source TEXT,
                message TEXT NOT NULL,
                campaign_id INTEGER,
                at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_applog_at ON app_log(id DESC);

            CREATE TABLE IF NOT EXISTS smtp_counters (
                smtp_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                sent_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (smtp_id, day)
            );
            """
        )
        _migrate(conn)


def _migrate(conn):
    """Add columns to existing DBs created before newer features."""
    smtp_cols = {r["name"] for r in conn.execute("PRAGMA table_info(smtp_servers)")}
    if "warmup_enabled" not in smtp_cols:
        conn.execute("ALTER TABLE smtp_servers ADD COLUMN warmup_enabled INTEGER NOT NULL DEFAULT 0")
    if "warmup_start" not in smtp_cols:
        conn.execute("ALTER TABLE smtp_servers ADD COLUMN warmup_start TEXT")
    if "reply_to" not in smtp_cols:
        conn.execute("ALTER TABLE smtp_servers ADD COLUMN reply_to TEXT")

    camp_cols = {r["name"] for r in conn.execute("PRAGMA table_info(campaigns)")}
    if "smtp_ids" not in camp_cols:
        conn.execute("ALTER TABLE campaigns ADD COLUMN smtp_ids TEXT")
    if "send_gap_seconds" not in camp_cols:
        conn.execute("ALTER TABLE campaigns ADD COLUMN send_gap_seconds INTEGER NOT NULL DEFAULT 0")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------- App log ----------

def applog(level, message, source=None, campaign_id=None):
    with _LOCK, get_conn() as conn:
        conn.execute(
            "INSERT INTO app_log (level, source, message, campaign_id, at) VALUES (?, ?, ?, ?, ?)",
            (level, source, message, campaign_id, now_iso()),
        )


def get_logs(after_id=0, limit=200, campaign_id=None):
    with get_conn() as conn:
        if campaign_id:
            rows = conn.execute(
                "SELECT * FROM app_log WHERE id > ? AND campaign_id=? ORDER BY id ASC LIMIT ?",
                (after_id, campaign_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM app_log WHERE id > ? ORDER BY id ASC LIMIT ?",
                (after_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]


# ---------- SMTP servers ----------

# Default warmup ramp — caps for day 1, 2, 3 ... After the schedule ends,
# the server's full daily_limit applies. Industry-standard gradual ramp.
WARMUP_SCHEDULE = [50, 100, 250, 500, 1000, 2000, 3500, 5000, 7500, 10000]


def warmup_day_index(warmup_start):
    """1-based day number since warmup_start (in UTC). Returns None if no start."""
    if not warmup_start:
        return None
    try:
        start = datetime.fromisoformat(warmup_start)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    delta = datetime.now(timezone.utc).date() - start.date()
    return delta.days + 1


def effective_daily_limit(smtp_row):
    """Daily cap honoring warmup ramp if enabled."""
    base = int(smtp_row.get("daily_limit", 500))
    if not smtp_row.get("warmup_enabled"):
        return base
    day = warmup_day_index(smtp_row.get("warmup_start"))
    if not day or day < 1:
        return min(base, WARMUP_SCHEDULE[0])
    if day > len(WARMUP_SCHEDULE):
        return base
    return min(base, WARMUP_SCHEDULE[day - 1])


def add_smtp(data):
    warmup_enabled = int(bool(data.get("warmup_enabled", False)))
    warmup_start = now_iso() if warmup_enabled else None
    with _LOCK, get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO smtp_servers
                (name, host, port, username, password, from_email, from_name,
                 daily_limit, use_tls, status, warmup_enabled, warmup_start, reply_to, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'inactive', ?, ?, ?, ?)
            """,
            (
                data["name"], data["host"], data["port"], data["username"], data["password"],
                data["from_email"], data.get("from_name", ""),
                int(data.get("daily_limit", 500)), int(bool(data.get("use_tls", True))),
                warmup_enabled, warmup_start, data.get("reply_to") or None,
                now_iso(),
            ),
        )
        return cur.lastrowid


def update_smtp(smtp_id, data):
    """Update an existing SMTP server's editable fields."""
    fields = {
        "name": data["name"], "host": data["host"], "port": int(data["port"]),
        "username": data["username"], "from_email": data["from_email"],
        "from_name": data.get("from_name", ""), "daily_limit": int(data.get("daily_limit", 500)),
        "use_tls": int(bool(data.get("use_tls", True))), "reply_to": data.get("reply_to") or None,
    }
    # Only overwrite password if a new one was supplied (keep old if blank)
    if data.get("password"):
        fields["password"] = data["password"]
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [smtp_id]
    with _LOCK, get_conn() as conn:
        conn.execute(f"UPDATE smtp_servers SET {cols} WHERE id=?", vals)


def set_warmup(smtp_id, enabled):
    with _LOCK, get_conn() as conn:
        if enabled:
            conn.execute(
                "UPDATE smtp_servers SET warmup_enabled=1, warmup_start=? WHERE id=?",
                (now_iso(), smtp_id),
            )
        else:
            conn.execute(
                "UPDATE smtp_servers SET warmup_enabled=0 WHERE id=?",
                (smtp_id,),
            )


def update_smtp_status(smtp_id, status, error=None):
    with _LOCK, get_conn() as conn:
        conn.execute(
            "UPDATE smtp_servers SET status=?, last_tested_at=?, last_test_error=? WHERE id=?",
            (status, now_iso(), error, smtp_id),
        )


def list_smtps():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM smtp_servers ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]


def get_smtp(smtp_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM smtp_servers WHERE id=?", (smtp_id,)).fetchone()
        return dict(row) if row else None


def delete_smtp(smtp_id):
    with _LOCK, get_conn() as conn:
        conn.execute("DELETE FROM smtp_servers WHERE id=?", (smtp_id,))


def list_active_smtps():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM smtp_servers WHERE status='active' ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- Campaigns ----------

def create_campaign(data):
    with _LOCK, get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO campaigns
                (name, subject, html_body, text_body, status, scheduled_at,
                 spam_score, spam_report, smtp_ids, send_gap_seconds, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["name"], data["subject"], data["html_body"], data["text_body"],
                data.get("status", "draft"), data.get("scheduled_at"),
                data.get("spam_score"), data.get("spam_report"),
                data.get("smtp_ids"), int(data.get("send_gap_seconds", 0) or 0),
                now_iso(),
            ),
        )
        return cur.lastrowid


def delete_campaign(campaign_id):
    with _LOCK, get_conn() as conn:
        conn.execute("DELETE FROM recipients WHERE campaign_id=?", (campaign_id,))
        conn.execute("DELETE FROM campaigns WHERE id=?", (campaign_id,))


def update_campaign(campaign_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [campaign_id]
    with _LOCK, get_conn() as conn:
        conn.execute(f"UPDATE campaigns SET {cols} WHERE id=?", vals)


def get_campaign(campaign_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        return dict(row) if row else None


def list_campaigns():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM campaigns ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def refresh_campaign_counts(campaign_id):
    with _LOCK, get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status IN ('sent','opened') THEN 1 ELSE 0 END) AS sent,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status='opened' OR opened_at IS NOT NULL THEN 1 ELSE 0 END) AS opened
            FROM recipients WHERE campaign_id=?
            """,
            (campaign_id,),
        ).fetchone()
        conn.execute(
            "UPDATE campaigns SET total=?, sent=?, failed=?, opened=? WHERE id=?",
            (row["total"] or 0, row["sent"] or 0, row["failed"] or 0, row["opened"] or 0, campaign_id),
        )


def claim_scheduled_campaigns():
    """Return campaign IDs whose scheduled_at <= now and status='scheduled'."""
    nowi = now_iso()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM campaigns WHERE status='scheduled' AND scheduled_at <= ?",
            (nowi,),
        ).fetchall()
        return [r["id"] for r in rows]


# ---------- Recipients ----------

def add_recipient(campaign_id, email, first_name, last_name, company, tracking_id):
    with _LOCK, get_conn() as conn:
        try:
            conn.execute(
                """
                INSERT INTO recipients
                    (campaign_id, email, first_name, last_name, company, tracking_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (campaign_id, email, first_name, last_name, company, tracking_id, now_iso()),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def fetch_pending_for_campaign(campaign_id, limit):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM recipients
            WHERE campaign_id=? AND status IN ('pending','retry')
            ORDER BY id ASC LIMIT ?
            """,
            (campaign_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def count_pending(campaign_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM recipients WHERE campaign_id=? AND status IN ('pending','retry')",
            (campaign_id,),
        ).fetchone()
        return row["c"]


def list_recipients(campaign_id, limit=200, offset=0):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM recipients WHERE campaign_id=? ORDER BY id ASC LIMIT ? OFFSET ?",
            (campaign_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_sent(recipient_id, smtp_id):
    with _LOCK, get_conn() as conn:
        conn.execute(
            "UPDATE recipients SET status='sent', smtp_used=?, sent_at=?, attempts=attempts+1, last_error=NULL WHERE id=?",
            (str(smtp_id), now_iso(), recipient_id),
        )
        conn.execute(
            "INSERT INTO send_log (recipient_id, smtp_id, ok, at) VALUES (?, ?, 1, ?)",
            (recipient_id, smtp_id, now_iso()),
        )
        conn.execute(
            """
            INSERT INTO smtp_counters (smtp_id, day, sent_count) VALUES (?, ?, 1)
            ON CONFLICT(smtp_id, day) DO UPDATE SET sent_count = sent_count + 1
            """,
            (smtp_id, today_str()),
        )


def mark_failed(recipient_id, smtp_id, error, retryable):
    new_status = "retry" if retryable else "failed"
    with _LOCK, get_conn() as conn:
        conn.execute(
            "UPDATE recipients SET status=?, last_error=?, attempts=attempts+1 WHERE id=?",
            (new_status, str(error)[:500], recipient_id),
        )
        conn.execute(
            "INSERT INTO send_log (recipient_id, smtp_id, ok, error, at) VALUES (?, ?, 0, ?, ?)",
            (recipient_id, smtp_id, str(error)[:500], now_iso()),
        )


def get_smtp_sent_today(smtp_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT sent_count FROM smtp_counters WHERE smtp_id=? AND day=?",
            (smtp_id, today_str()),
        ).fetchone()
        return row["sent_count"] if row else 0


def record_open(tracking_id, ip, user_agent):
    with _LOCK, get_conn() as conn:
        conn.execute(
            "INSERT INTO open_log (tracking_id, ip, user_agent, at) VALUES (?, ?, ?, ?)",
            (tracking_id, ip, user_agent, now_iso()),
        )
        conn.execute(
            """
            UPDATE recipients
            SET open_count = open_count + 1,
                opened_at = COALESCE(opened_at, ?),
                status = CASE WHEN status='sent' THEN 'opened' ELSE status END
            WHERE tracking_id = ?
            """,
            (now_iso(), tracking_id),
        )


# ---------- Stats ----------

def per_domain_stats():
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                LOWER(SUBSTR(email, INSTR(email,'@')+1)) AS domain,
                COUNT(*) AS total,
                SUM(CASE WHEN status IN ('sent','opened') THEN 1 ELSE 0 END) AS sent,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN opened_at IS NOT NULL THEN 1 ELSE 0 END) AS opened
            FROM recipients
            WHERE INSTR(email,'@') > 0
            GROUP BY domain
            ORDER BY total DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def per_smtp_stats():
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.name, s.host, s.daily_limit, s.status,
                   s.warmup_enabled, s.warmup_start,
                   COALESCE(SUM(c.sent_count), 0) AS sent_today
            FROM smtp_servers s
            LEFT JOIN smtp_counters c ON c.smtp_id = s.id AND c.day = ?
            GROUP BY s.id
            ORDER BY s.id ASC
            """,
            (today_str(),),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["effective_limit"] = effective_daily_limit(d)
            d["warmup_day"] = warmup_day_index(d.get("warmup_start")) if d.get("warmup_enabled") else None
            out.append(d)
        return out


def overall_stats():
    with get_conn() as conn:
        cmp_row = conn.execute(
            """
            SELECT
              COUNT(*) AS campaigns_total,
              SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running,
              SUM(CASE WHEN status='scheduled' THEN 1 ELSE 0 END) AS scheduled,
              SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed
            FROM campaigns
            """
        ).fetchone()
        rcp_row = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN status IN ('sent','opened') THEN 1 ELSE 0 END) AS sent,
              SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
              SUM(CASE WHEN opened_at IS NOT NULL THEN 1 ELSE 0 END) AS opened,
              SUM(CASE WHEN status IN ('pending','retry') THEN 1 ELSE 0 END) AS pending
            FROM recipients
            """
        ).fetchone()
        sent_today = conn.execute(
            "SELECT COALESCE(SUM(sent_count),0) AS s FROM smtp_counters WHERE day=?",
            (today_str(),),
        ).fetchone()["s"]
        return {
            "campaigns": dict(cmp_row),
            "recipients": dict(rcp_row),
            "sent_today": sent_today,
        }
