"""SMTP test + send helpers used by the worker and API."""

import smtplib
import socket
import ssl
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

from jinja2 import Template


def test_connection(host, port, username, password, use_tls=True, timeout=15, ehlo_name=None):
    """Try to connect, auth and quit. Returns (ok, error_str_or_None)."""
    try:
        port = int(port)
        context = ssl.create_default_context()
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context,
                                      local_hostname=ehlo_name)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout, local_hostname=ehlo_name)
            server.ehlo()
            if use_tls:
                server.starttls(context=context)
                server.ehlo()
        try:
            if username:
                server.login(username, password)
            server.noop()
        finally:
            try:
                server.quit()
            except Exception:
                pass
        return True, None
    except (socket.timeout, TimeoutError):
        return False, "Connection timed out"
    except smtplib.SMTPAuthenticationError as e:
        return False, f"Authentication failed: {e.smtp_error.decode() if isinstance(e.smtp_error, bytes) else e}"
    except smtplib.SMTPException as e:
        return False, f"SMTP error: {e}"
    except OSError as e:
        return False, f"Network error: {e}"
    except Exception as e:
        return False, f"Unexpected: {e}"


def render(tmpl_str, ctx):
    return Template(tmpl_str).render(**ctx)


HTML_WRAPPER = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{subject}</title>
</head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;color:#222;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f5f5f5;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;background:#ffffff;border-radius:8px;">
<tr><td style="padding:32px 36px;font-size:15px;line-height:1.6;color:#222;">
{body}
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _wrap_html(inner_html, subject):
    """Wrap raw HTML fragment in a clean HTML5 email-friendly shell."""
    low = inner_html.lower()
    if "<!doctype" in low or "<html" in low:
        return inner_html
    safe_subject = subject.replace("<", "&lt;").replace(">", "&gt;")
    return HTML_WRAPPER.format(subject=safe_subject, body=inner_html)


def build_message(smtp_cfg, recipient, subject, html_body, text_body,
                  tracking_base_url, unsubscribe_url, reply_to):
    ctx = {
        "first_name": recipient.get("first_name") or "there",
        "last_name": recipient.get("last_name") or "",
        "company": recipient.get("company") or "",
        "email": recipient["email"],
    }
    subject_r = render(subject, ctx)
    html_inner = render(html_body, ctx)
    text_r = render(text_body, ctx)

    tracking_id = recipient["tracking_id"]
    if tracking_base_url:
        pixel = (
            f'<img src="{tracking_base_url.rstrip("/")}/track/open/{tracking_id}.png" '
            f'width="1" height="1" alt="" style="display:none;border:0;outline:none;text-decoration:none;" />'
        )
        html_inner = html_inner + pixel

    if unsubscribe_url:
        unsub = (
            f'<p style="font-size:11px;color:#888;margin-top:28px;border-top:1px solid #eee;padding-top:14px;">'
            f'You received this because you subscribed to updates from '
            f'{smtp_cfg.get("from_name") or smtp_cfg["from_email"]}. '
            f'<a href="{unsubscribe_url}?e={recipient["email"]}" style="color:#888;">Unsubscribe</a>.'
            f'</p>'
        )
        html_inner += unsub
        text_r += f"\n\nUnsubscribe: {unsubscribe_url}?e={recipient['email']}"

    html_r = _wrap_html(html_inner, subject_r)

    msg = MIMEMultipart("alternative")
    from_email = smtp_cfg["from_email"]
    from_name = smtp_cfg.get("from_name") or from_email
    from_domain = from_email.split("@")[-1]

    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = recipient["email"]
    msg["Subject"] = subject_r
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=from_domain)
    # Reply-To must align with From-domain when possible (alignment helps DMARC)
    if reply_to:
        msg["Reply-To"] = reply_to
    else:
        msg["Reply-To"] = from_email
    if unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}?e={recipient['email']}>, <mailto:{from_email}?subject=unsubscribe>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    # Identify as bulk legitimately — helps engagement-based filters bucket properly
    msg["Precedence"] = "bulk"
    msg["Auto-Submitted"] = "auto-generated"
    # NOTE: deliberately NOT setting X-Mailer — that header on bulk mail is a known
    # spam signal when it doesn't match a real mail client.

    msg.attach(MIMEText(text_r, "plain", "utf-8"))
    msg.attach(MIMEText(html_r, "html", "utf-8"))
    return msg


def send_message(smtp_cfg, msg, to_email, timeout=30):
    port = int(smtp_cfg["port"])
    context = ssl.create_default_context()
    # EHLO with the sender's own domain — generic "localhost" is a spam signal
    ehlo_name = smtp_cfg["from_email"].split("@")[-1]
    if port == 465:
        server = smtplib.SMTP_SSL(smtp_cfg["host"], port, timeout=timeout, context=context,
                                  local_hostname=ehlo_name)
    else:
        server = smtplib.SMTP(smtp_cfg["host"], port, timeout=timeout, local_hostname=ehlo_name)
        server.ehlo(ehlo_name)
        if smtp_cfg.get("use_tls", 1):
            server.starttls(context=context)
            server.ehlo(ehlo_name)
    try:
        if smtp_cfg.get("username"):
            server.login(smtp_cfg["username"], smtp_cfg["password"])
        server.sendmail(smtp_cfg["from_email"], [to_email], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass
