import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TIMEZONE_NAME = os.getenv("TIMEZONE", "Europe/Madrid")
TZ = ZoneInfo(TIMEZONE_NAME)

MAX_DELAY_SECONDS = 30 * 24 * 3600       # 30 days
MAX_HORIZON = timedelta(days=365)         # reject absolute dates further than this


def now():
    return datetime.now(TZ)


def current_datetime_label():
    """What gets shown to the LLM as ground truth for "now"."""
    return f"CURRENT_DATETIME={now().isoformat()}\nTIMEZONE={TIMEZONE_NAME}"


def validate_delay_seconds(value):
    """Validates a relative delay (e.g. from "en 10 minutos"). Returns an
    absolute ISO datetime string, or None if invalid."""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0 or seconds > MAX_DELAY_SECONDS:
        return None
    return (now() + timedelta(seconds=seconds)).isoformat()


def validate_absolute_datetime(value):
    """Validates an absolute datetime string the LLM produced (e.g. from
    "mañana a las 9"). Returns a normalized ISO string, or None if invalid,
    in the past, or too far in the future."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    current = now()
    if parsed <= current or parsed > current + MAX_HORIZON:
        return None
    return parsed.isoformat()


def human_time(iso_value):
    """Short, natural rendering of an ISO datetime for confirmation messages."""
    try:
        dt = datetime.fromisoformat(iso_value)
    except ValueError:
        return iso_value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    today = now().date()
    if dt.date() == today:
        return f"hoy a las {dt.strftime('%H:%M')}"
    if dt.date() == today + timedelta(days=1):
        return f"mañana a las {dt.strftime('%H:%M')}"
    return dt.strftime("el %d/%m a las %H:%M")
