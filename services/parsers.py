"""File parsers: docx → html/text content, xlsx/csv → recipient list."""

import csv
import io
import re
from html import escape

from docx import Document
import openpyxl


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


# ---------------- Content parsing ----------------

def parse_content_file(filename, raw_bytes):
    """Return (subject_guess, html_body, text_body) from an uploaded file."""
    name = filename.lower()
    if name.endswith(".docx"):
        return _parse_docx(raw_bytes)
    if name.endswith(".html") or name.endswith(".htm"):
        return _parse_html(raw_bytes.decode("utf-8", errors="replace"))
    if name.endswith(".txt"):
        return _parse_txt(raw_bytes.decode("utf-8", errors="replace"))
    raise ValueError("Unsupported content file. Use .docx, .html, or .txt")


def _parse_docx(raw_bytes):
    doc = Document(io.BytesIO(raw_bytes))
    lines = []
    for p in doc.paragraphs:
        lines.append(p.text)
    text = "\n".join(lines).strip()
    if not text:
        text = ""
    return _build_from_text(text)


def _parse_txt(raw_text):
    return _build_from_text(raw_text.strip())


def _parse_html(raw_html):
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = re.sub(r"\s+", " ", text).strip()
    subject = _guess_subject(text)
    return subject, raw_html, text


def _build_from_text(text):
    """Split a plain-text email into a clean HTML body + text body."""
    subject = _guess_subject(text)

    # split into paragraphs on blank lines
    blocks = re.split(r"\n\s*\n", text)
    html_parts = []
    for b in blocks:
        b = b.strip()
        if not b:
            continue
        # single-line inside the block -> still wrap in <p>, preserve internal newlines with <br/>
        safe = escape(b).replace("\n", "<br/>")
        html_parts.append(f"<p>{safe}</p>")
    html_body = "\n".join(html_parts)
    return subject, html_body, text


def _guess_subject(text):
    if not text:
        return "(no subject)"
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if len(first_line) > 90:
        first_line = first_line[:87] + "..."
    return first_line or "(no subject)"


# ---------------- Recipient parsing ----------------

def parse_recipients_file(filename, raw_bytes):
    """Return a list of dicts: {email, first_name, last_name, company, raw_row}."""
    name = filename.lower()
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        rows = _read_xlsx_rows(raw_bytes)
    elif name.endswith(".csv"):
        rows = _read_csv_rows(raw_bytes)
    else:
        raise ValueError("Unsupported recipients file. Use .xlsx or .csv")

    if not rows:
        return [], []

    header = [str(c or "").strip().lower() for c in rows[0]]
    email_idx = _find_col(header, ["email", "contact email", "e-mail", "mail"])
    first_idx = _find_col(header, ["first name", "first_name", "firstname", "fname", "given name"])
    last_idx = _find_col(header, ["last name", "last_name", "lastname", "lname", "surname"])
    name_idx = _find_col(header, ["owner name", "name", "full name", "contact name"])
    company_idx = _find_col(header, ["company", "organization", "org", "business"])

    if email_idx is None:
        # fallback: first column that looks like an email in row 2
        if len(rows) > 1:
            for i, cell in enumerate(rows[1]):
                if cell and EMAIL_RE.match(str(cell).split("|")[0].strip()):
                    email_idx = i
                    break
        if email_idx is None:
            raise ValueError("Could not detect an email column in the file.")

    seen = set()
    recipients = []
    invalid = []

    for r in rows[1:]:
        if not r or all(c is None or str(c).strip() == "" for c in r):
            continue
        raw_email_cell = str(r[email_idx] or "").strip() if email_idx < len(r) else ""
        if not raw_email_cell:
            continue
        # split on |  or  ;  or  ,
        emails = re.split(r"[|;,]", raw_email_cell)

        owner_name = ""
        first_name = ""
        last_name = ""
        if first_idx is not None and first_idx < len(r):
            first_name = str(r[first_idx] or "").strip()
        if last_idx is not None and last_idx < len(r):
            last_name = str(r[last_idx] or "").strip()
        if not first_name and not last_name and name_idx is not None and name_idx < len(r):
            owner_name = str(r[name_idx] or "").strip()
            parts = owner_name.split()
            first_name = parts[0] if parts else ""
            last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
        company = ""
        if company_idx is not None and company_idx < len(r):
            company = str(r[company_idx] or "").strip()

        for em in emails:
            em = em.strip().lower()
            if not em:
                continue
            if not EMAIL_RE.match(em):
                invalid.append({"email": em, "reason": "invalid_syntax"})
                continue
            if em in seen:
                continue
            seen.add(em)
            recipients.append({
                "email": em,
                "first_name": first_name,
                "last_name": last_name,
                "company": company,
            })

    return recipients, invalid


def _find_col(header, names):
    for n in names:
        if n in header:
            return header.index(n)
    return None


def _read_xlsx_rows(raw_bytes):
    wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True, read_only=True)
    ws = wb.active
    return [list(row) for row in ws.iter_rows(values_only=True)]


def _read_csv_rows(raw_bytes):
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    return [r for r in reader]
