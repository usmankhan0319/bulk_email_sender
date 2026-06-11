"""Open-tracking web server.

Email mein chhota 1x1 transparent pixel hota hai:
    <img src="{TRACKING_BASE_URL}/track/open/{tracking_id}.png" />

Jab user email open karta hai, uska email client yeh pixel load karta hai,
aur yahan request aati hai. Hum DB mein open log kar lete hain.

Run:
    python tracker.py
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, request, send_file

import db

load_dotenv()

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "tracker.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("tracker")

PIXEL_PATH = Path(__file__).parent / "pixel.png"


def ensure_pixel():
    """1x1 transparent PNG banao agar exist nahi karta."""
    if PIXEL_PATH.exists():
        return
    # Hard-coded smallest 1x1 transparent PNG bytes
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfc\xff"
        b"\xff?\x03\x00\x05\xfe\x02\xfe\xa3I[\xe6\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    PIXEL_PATH.write_bytes(png_bytes)


app = Flask(__name__)


@app.route("/track/open/<tracking_id>.png")
def track_open(tracking_id):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    ua = request.headers.get("User-Agent", "")
    try:
        db.record_open(tracking_id, ip, ua)
        log.info("OPEN  %s  ip=%s  ua=%s", tracking_id, ip, ua[:80])
    except Exception as e:
        log.exception("Failed to record open: %s", e)
    response = send_file(PIXEL_PATH, mimetype="image/png")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/health")
def health():
    return {"ok": True, "stats": db.stats()}


if __name__ == "__main__":
    db.init_db()
    ensure_pixel()
    host = os.getenv("TRACKER_HOST", "0.0.0.0")
    port = int(os.getenv("TRACKER_PORT", "8080"))
    log.info("Tracker server starting on %s:%s", host, port)
    app.run(host=host, port=port, threaded=True)
