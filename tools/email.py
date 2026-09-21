import os
import re
import smtplib
import logging
from email.mime.text import MIMEText

import db

logger = logging.getLogger("asimov")

SMTP_HOST = os.getenv("EMAIL_SMTP_HOST")
SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "587"))
SMTP_USER = os.getenv("EMAIL_USER")
SMTP_PASSWORD = os.getenv("EMAIL_PASSWORD")
SMTP_FROM = os.getenv("EMAIL_FROM", SMTP_USER)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _parse_contacts():
    raw = os.getenv("CONTACTS", "")
    contacts = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        name, _, email_addr = pair.partition(":")
        contacts[name.strip().lower()] = email_addr.strip()
    return contacts


CONTACTS = _parse_contacts()


def is_valid_email(value):
    return bool(value) and bool(_EMAIL_RE.match(value.strip()))


def resolve_recipient(to_field):
    """Returns (email, display_name) if `to_field` is already an address or
    a known contact name; (None, to_field) if it can't be resolved — the LLM
    is never trusted to have invented a valid address."""
    if not to_field:
        return None, None
    to_field = to_field.strip()
    if is_valid_email(to_field):
        return to_field, to_field
    email_addr = CONTACTS.get(to_field.lower())
    if email_addr:
        return email_addr, to_field
    return None, to_field


def create_draft(user_id, to_field, subject, body):
    email_addr, display_name = resolve_recipient(to_field)
    if not email_addr:
        return {"status": "unknown_contact", "to_name": display_name}
    draft_id = db.create_email_draft(user_id, email_addr, display_name, subject, body)
    return {"id": draft_id, "to_email": email_addr, "to_name": display_name, "subject": subject, "body": body}


def send(user_id, draft_id):
    draft = db.get_email_draft(draft_id, user_id)
    if not draft:
        return {"sent": False, "error": "draft_not_found"}
    _, to_email, to_name, subject, body, status = draft
    if status == "sent":
        return {"sent": False, "error": "already_sent"}
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        return {"sent": False, "error": "smtp_not_configured"}

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_email], msg.as_string())
    except Exception:
        logger.error(f"Failed to send email draft {draft_id}", exc_info=True)
        return {"sent": False, "error": "smtp_error"}

    db.mark_email_sent(draft_id)
    return {"sent": True, "to_email": to_email, "subject": subject}
