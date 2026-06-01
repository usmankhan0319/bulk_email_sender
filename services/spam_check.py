"""Lightweight spam-score heuristic for outgoing email content."""

import re
from html import unescape

SPAM_WORDS = [
    "free", "win", "winner", "cash", "prize", "guarantee", "guaranteed",
    "act now", "urgent", "limited time", "click here", "click below",
    "100%", "$$$", "earn money", "make money", "double your", "risk free",
    "viagra", "no cost", "no obligation", "credit card", "lowest price",
    "amazing", "incredible deal", "buy now", "order now", "miracle",
    "weight loss", "lose weight", "extra income", "online biz",
    "casino", "lottery", "investment", "cryptocurrency",
]


def _strip_html(html):
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S | re.I)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def analyze(subject, html_body, text_body, has_unsubscribe):
    issues = []
    score = 100

    plain = _strip_html(html_body) if html_body else (text_body or "")
    combined = f"{subject}\n{plain}".lower()

    # 1) Spam trigger words
    hits = []
    for w in SPAM_WORDS:
        if re.search(rf"\b{re.escape(w)}\b", combined):
            hits.append(w)
    if hits:
        penalty = min(30, 4 * len(hits))
        score -= penalty
        issues.append({
            "severity": "high" if penalty >= 16 else "medium",
            "message": f"Spam-trigger words detected: {', '.join(hits[:8])}",
            "fix": "Reword or remove these phrases — they are common spam-filter flags.",
        })

    # 2) Subject all caps / shouting
    if subject:
        letters = re.sub(r"[^A-Za-z]", "", subject)
        if len(letters) >= 6 and letters.isupper():
            score -= 10
            issues.append({
                "severity": "medium",
                "message": "Subject line is ALL CAPS",
                "fix": "Use sentence case or title case in the subject.",
            })
        if subject.count("!") >= 2:
            score -= 8
            issues.append({
                "severity": "medium",
                "message": "Subject contains multiple '!' exclamations",
                "fix": "Use at most one exclamation point.",
            })
        if len(subject) > 90:
            score -= 5
            issues.append({
                "severity": "low",
                "message": f"Subject is quite long ({len(subject)} chars)",
                "fix": "Keep subject under ~60 characters for best deliverability.",
            })

    # 3) HTML/text ratio + plain-text version
    if not text_body or len(text_body.strip()) < 20:
        score -= 12
        issues.append({
            "severity": "high",
            "message": "No plain-text version of the email",
            "fix": "Emails should always include a plain-text alternative — HTML-only is a spam signal.",
        })

    # 4) Link-to-text ratio
    if html_body:
        links = re.findall(r"<a\s+[^>]*href=", html_body, flags=re.I)
        words = max(1, len(plain.split()))
        link_density = len(links) / words
        if len(links) > 0 and link_density > 0.08:
            score -= 10
            issues.append({
                "severity": "medium",
                "message": f"High link-to-text ratio ({len(links)} links, {words} words)",
                "fix": "Reduce the number of links, or add more text content.",
            })

        # 5) Image-only email
        imgs = re.findall(r"<img\s", html_body, flags=re.I)
        if len(plain) < 80 and len(imgs) > 0:
            score -= 15
            issues.append({
                "severity": "high",
                "message": "Email appears to be image-heavy with little text",
                "fix": "Add meaningful text content — image-only emails are flagged as spam.",
            })

    # 6) Unsubscribe link
    if not has_unsubscribe and not re.search(r"unsubscribe", combined):
        score -= 8
        issues.append({
            "severity": "medium",
            "message": "No unsubscribe link detected",
            "fix": "Include a visible unsubscribe link and a List-Unsubscribe header.",
        })

    # 7) URL shorteners
    if re.search(r"\b(bit\.ly|tinyurl|t\.co|goo\.gl|ow\.ly)\b", combined):
        score -= 8
        issues.append({
            "severity": "medium",
            "message": "URL shortener detected in content",
            "fix": "Use full URLs instead of shorteners — they're a common spam signal.",
        })

    score = max(0, min(100, score))
    if score >= 80:
        verdict = "good"
    elif score >= 60:
        verdict = "warn"
    else:
        verdict = "bad"

    return {
        "score": score,
        "verdict": verdict,
        "issues": issues,
    }
