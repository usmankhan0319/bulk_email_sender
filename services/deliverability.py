"""DNS-based deliverability checker for a sending domain.

Looks up:
  - SPF (TXT record starting with v=spf1)
  - DMARC (TXT record at _dmarc.<domain>)
  - DKIM hint (common selectors: default, google, k1, s1, selector1, mail)
  - MX records

Returns a structured report with severity and a "fix" for each issue.
"""

import re

import dns.resolver
import dns.exception


COMMON_DKIM_SELECTORS = [
    "default", "google", "k1", "k2", "s1", "s2",
    "selector1", "selector2", "mail", "smtpapi", "mxvault", "mandrill",
    "dkim", "everlytickey1", "sendgrid", "mailgun",
    # Titan / cPanel / web-hosting providers
    "titan1", "titan2", "titan", "x", "cm", "dkim1", "dkim2",
    "scph0819", "scph1019", "scph0920", "hostingermail",
    "zoho", "zmail", "amazonses", "pm", "fm1", "fm2",
]


def _txt_records(name, timeout=5):
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = timeout
        resolver.lifetime = timeout
        answers = resolver.resolve(name, "TXT")
        return [b"".join(r.strings).decode("utf-8", errors="replace") for r in answers]
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN,
            dns.exception.Timeout, dns.resolver.NoNameservers):
        return []
    except Exception:
        return []


def _mx_records(name, timeout=5):
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = timeout
        resolver.lifetime = timeout
        answers = resolver.resolve(name, "MX")
        return [(int(r.preference), str(r.exchange).rstrip(".")) for r in answers]
    except Exception:
        return []


def check_domain(domain, selector=None):
    domain = domain.strip().lower().lstrip("@")
    issues = []
    found = {"spf": None, "dmarc": None, "dkim": [], "mx": []}
    score = 100

    # If the user gave a custom selector, probe it first.
    selectors_to_try = list(COMMON_DKIM_SELECTORS)
    if selector:
        selector = selector.strip().lower().replace("._domainkey", "").rstrip(".")
        if selector and selector not in selectors_to_try:
            selectors_to_try.insert(0, selector)

    # MX
    mx = _mx_records(domain)
    found["mx"] = mx
    if not mx:
        score -= 15
        issues.append({
            "severity": "medium",
            "title": "No MX records",
            "detail": f"{domain} has no MX records — receiving mail won't work.",
            "fix": "Add MX records pointing to your mail host (e.g., your hosting provider).",
        })

    # SPF
    txts = _txt_records(domain)
    spf = next((t for t in txts if t.lower().startswith("v=spf1")), None)
    found["spf"] = spf
    if not spf:
        score -= 35
        issues.append({
            "severity": "high",
            "title": "No SPF record",
            "detail": f"No `v=spf1` TXT record found on {domain}. This is the #1 reason emails land in spam.",
            "fix": (
                "Add a TXT record at the root of your domain. Example for SendGrid:\n"
                "  v=spf1 include:sendgrid.net ~all\n"
                "For Mailgun:  v=spf1 include:mailgun.org ~all\n"
                "For Gmail:    v=spf1 include:_spf.google.com ~all"
            ),
        })
    elif "+all" in spf:
        score -= 10
        issues.append({
            "severity": "medium",
            "title": "SPF uses '+all' (too permissive)",
            "detail": "Anyone can send mail claiming to be from your domain — ignored by most receivers.",
            "fix": "Change `+all` to `~all` (soft-fail) or `-all` (hard-fail).",
        })

    # DMARC
    dmarc_txts = _txt_records(f"_dmarc.{domain}")
    dmarc = next((t for t in dmarc_txts if t.lower().startswith("v=dmarc1")), None)
    found["dmarc"] = dmarc
    if not dmarc:
        score -= 25
        issues.append({
            "severity": "high",
            "title": "No DMARC record",
            "detail": f"No DMARC policy at _dmarc.{domain}. Gmail now requires DMARC for bulk senders.",
            "fix": (
                "Add a TXT record at _dmarc." + domain + " :\n"
                "  v=DMARC1; p=none; rua=mailto:dmarc@" + domain + "; fo=1\n"
                "Start with p=none to monitor, then graduate to p=quarantine."
            ),
        })

    # DKIM (best effort — probe known + user-supplied selectors)
    dkim_hits = []
    for sel in selectors_to_try:
        recs = _txt_records(f"{sel}._domainkey.{domain}")
        for r in recs:
            if "p=" in r.lower() or "v=dkim1" in r.lower():
                dkim_hits.append({"selector": sel, "snippet": r[:120]})
                break
        if dkim_hits and len(dkim_hits) >= 2:
            break
    found["dkim"] = dkim_hits
    if not dkim_hits:
        score -= 25
        issues.append({
            "severity": "high",
            "title": "No DKIM record detected",
            "detail": (
                f"Couldn't find DKIM at the common selectors for {domain}. "
                "Agar aapne DKIM set kiya hai (e.g. Titan = titan1), to apna selector "
                "input box mein daal kar dobara check karein. "
                "DKIM cryptographically signs your emails — Gmail/Outlook expect it for bulk."
            ),
            "fix": (
                "Get the DKIM TXT record from your SMTP provider's dashboard "
                "(SendGrid → Sender Authentication; Mailgun → Domains → DNS records). "
                "Add it at <selector>._domainkey." + domain
            ),
        })

    score = max(0, min(100, score))
    verdict = "good" if score >= 80 else ("warn" if score >= 50 else "bad")

    return {
        "domain": domain,
        "score": score,
        "verdict": verdict,
        "found": found,
        "issues": issues,
    }
