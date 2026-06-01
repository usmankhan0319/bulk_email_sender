# Bulk Email Sender — FastAPI Dashboard (Roman Urdu Guide)

Full-stack bulk email tool with a dark themed web dashboard:

- **Multiple SMTP servers** — frontend se add karo, auto connection-test, active/inactive status
- **Content upload** — `.docx` / `.html` / `.txt` upload karo, auto-parse to HTML + plain-text
- **Recipients upload** — `.xlsx` / `.csv` upload karo, multi-email cells (`|` separated) auto-split
- **Spam check** — bhejne se pehle content scan, score + actionable fix suggestions
- **Send now / Schedule** — abhi run karo ya date-time pe schedule karo
- **Live logs + stats** — per-domain, per-SMTP, open-tracking pixel, sab dashboard pe
- **Background worker** — app start hote hi automatic, scheduled campaigns aur pending sends handle karta hai

---

## 1. Project structure

```
bulk_email_sender/
├── app.py                  # FastAPI app (routes + tracker pixel)
├── worker.py               # background sender thread (auto-starts with app)
├── db.py                   # SQLite helper
├── services/
│   ├── parsers.py          # docx/xlsx/csv parsing
│   ├── smtp_ops.py         # SMTP test + send
│   └── spam_check.py       # spam-score heuristic
├── templates/dashboard.html
├── static/css/app.css
├── static/js/app.js
├── requirements.txt
├── .env.example
├── clients_data/           # client ke example files
└── mailer.db               # auto-banegi pehli run par
```

---

## 2. Setup (one-time)

```bash
cd /Users/muhammadshoaib/Desktop/NeurocareAi/bulk_email_sender

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# .env mein TRACKING_BASE_URL, DEFAULT_REPLY_TO, UNSUBSCRIBE_URL set karo
# (SMTP credentials ab frontend se add hote hain — env mein nahi)
```

---

## 3. Chalao

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

Phir browser mein open karo: **http://localhost:8000**

Bas — saari functionality dashboard se hi mil jayegi.

---

## 4. Workflow (dashboard se)

### Step 1 — SMTP servers add karo
- Sidebar mein **SMTP Servers** → click **+ Add SMTP server**
- Form fill karo: host, port, username, password (app-password), from-email, daily-limit
- **Save & test** → script khud connection check karegi
  - ✅ pass → status `active`, sending ke liye eligible
  - ❌ fail → status `inactive`, error reason dikhega
- Jitne servers chahiye add kar lo — har ek rotate hoga

### Step 2 — Campaign banao
- Sidebar mein **New Campaign**
- **1. Campaign details** — name + subject (subject auto-fill hota hai docx upload se)
- **2. Email content** — `.docx` / `.html` / `.txt` drop karo
  - Parser docx ko HTML mein convert karta hai paragraphs/line breaks preserve karke
  - **Run spam check** click karo — score, verdict, aur har issue ka fix suggestion
- **3. Recipients** — `.xlsx` / `.csv` drop karo
  - Auto column detection: `Email/Contact Email`, `Owner Name`, `First/Last Name`, `Company`
  - Multi-email cells (e.g. `info@x.com | pugh@x.com`) auto-split, dono separate recipients banenge
  - Invalid syntax skip ho jata hai, summary mein count dikhta hai
- **4. Send** — 3 options:
  - **Send now** — abhi turant queue mein daal do
  - **Save as draft** — DB mein save, baad mein manually start kar sakte ho
  - **Schedule** — date-time picker + click → us waqt automatic start

### Step 3 — Track progress
- **Dashboard** — overall tiles (sent today, opened, pending, failed) + per-domain table + per-SMTP usage
- **Campaigns** — har campaign ka live progress, sent/opened/failed counts
- **Live Logs** — har send / fail / open / SMTP test entry real-time

---

## 5. Open tracking

Har recipient ka unique UUID tracking-id banta hai. Email ke HTML mein hidden 1x1 pixel embed hota hai:
```html
<img src="{TRACKING_BASE_URL}/track/open/<tracking_id>.png" />
```
Recipient email open kare → pixel load hoga → `/track/open/...` endpoint hit → DB mein `open_log` + `recipients.opened_at` update.

**Important:** `TRACKING_BASE_URL` **public** hona chahiye. Local testing:
```bash
ngrok http 8000
# generated URL ko .env ke TRACKING_BASE_URL mein paste karo
```

---

## 6. Inbox vs Spam

### Script side (humne handle kiya):
- Multipart `text/plain + text/html`
- `From`, `Reply-To`, `Message-ID`, `Date`, `List-Unsubscribe`, `X-Mailer` headers
- Visible unsubscribe footer
- Spam-check report before send (score + fixes)
- SMTP rotation + per-server rate limit + jitter

### Domain side (aapko karna hai):
1. **SPF** record on sending domain
2. **DKIM** — apke SMTP provider ka DKIM record DNS mein
3. **DMARC** record
4. **Warm-up** — naye domain par day-1 par 5k mat bhejna. Gradually 50 → 200 → 500 → 1k → 5k
5. Gmail/Zoho daily limits ~500/day hote hain. 4-5k volume ke liye **Mailgun / SendGrid / Amazon SES** behtar

---

## 7. Production deploy

```bash
# Process manager use karo
nohup uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1 > logs/app.log 2>&1 &
```

`workers=1` is important — kyunki worker thread in-process hai, multiple worker processes = duplicate sends.

Better: systemd service + nginx reverse proxy + Let's Encrypt for HTTPS.

---

## 8. API quick reference

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Dashboard HTML |
| GET | `/api/stats` | Overall counters |
| GET | `/api/stats/domains` | Per-domain stats |
| GET | `/api/stats/smtps` | Per-SMTP usage |
| GET | `/api/logs?after=ID` | Tail activity log |
| GET | `/api/smtp` | List SMTP servers |
| POST | `/api/smtp` | Add + auto-test |
| POST | `/api/smtp/{id}/test` | Re-test |
| DELETE | `/api/smtp/{id}` | Remove |
| POST | `/api/upload/content` | Upload .docx/.html/.txt |
| POST | `/api/upload/recipients` | Upload .xlsx/.csv |
| POST | `/api/spam-check` | Body validation |
| POST | `/api/campaigns` | Create + (save \| start_now \| schedule) |
| GET | `/api/campaigns` | List all |
| POST | `/api/campaigns/{id}/start` | Manual start |
| POST | `/api/campaigns/{id}/pause` | Pause |
| POST | `/api/campaigns/{id}/resume` | Resume |
| GET | `/track/open/{tid}.png` | Open-tracking pixel |

---

## 9. Reset / clean slate

```bash
rm -f mailer.db
# next start naye empty DB se hoga
```

---

## 10. Troubleshooting

| Problem | Fix |
|---|---|
| "Authentication failed" on SMTP test | Gmail ko App Password chahiye, regular password nahi |
| "Connection timed out" | Port 465 (SSL) try karo `Use STARTTLS` uncheck kar ke |
| "Could not detect email column" | XLSX ki pehli row column-headers honi chahiye (Email/Contact Email/etc) |
| Opens 0 ho rahe | `TRACKING_BASE_URL` public URL hona chahiye, localhost nahi |
| Worker run nahi ho raha | Logs check karo (`logs/sender.log` ya app stdout) — `Worker thread started` line dikhni chahiye |
| Sab inbox nahi ja rahe | SPF/DKIM/DMARC DNS records check karo + domain warm-up karo |

---

## Legacy CLI (still works)

Old CLI mode bhi available hai for headless ops:
```bash
python bulk_sender.py --import recipients.csv content.csv
python bulk_sender.py --run
python bulk_sender.py --stats
```
But UI version (FastAPI dashboard) recommended hai.
