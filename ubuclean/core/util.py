"""Small utilities shared by the core and the UI."""

from __future__ import annotations

import math
import re
import time

_SIZE_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s*([KMGTPE]?)(?:i?B)?", re.IGNORECASE)
_SIZE_UNITS = {"": 0, "K": 1, "M": 2, "G": 3, "T": 4, "P": 5, "E": 6}
MONTHS_ID = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def format_date(timestamp: float, *, with_time: bool = False) -> str:
    """Format a date in English, optionally including the time."""
    moment = time.localtime(timestamp)
    date_text = f"{moment.tm_mday} {MONTHS_ID[moment.tm_mon - 1]} {moment.tm_year}"
    if with_time:
        return f"{date_text} · {moment.tm_hour:02d}:{moment.tm_min:02d}"
    return date_text


def parse_human_size(text: str) -> int:
    """Parse a size such as ``1.5 GB``, ``1,024 kB``, or ``771.6M`` into bytes."""
    match = _SIZE_RE.search(text)
    if not match:
        return 0
    raw_number = match.group(1)
    if "," in raw_number and "." not in raw_number and re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw_number):
        raw_number = raw_number.replace(",", "")
    else:
        raw_number = raw_number.replace(",", ".")
    number = float(raw_number)
    unit = match.group(2).upper()
    power = _SIZE_UNITS.get(unit, 0)
    return int(number * (1024**power))


def format_bytes(size: float, *, precision: int = 1) -> str:
    """Format a byte size into a compact string (example: ``2.4 GB``)."""
    size = max(0.0, float(size))
    if size < 1024:
        return f"{int(size)} B"
    units = ("kB", "MB", "GB", "TB", "PB")
    value = size
    unit = "B"
    for unit in units:
        value /= 1024.0
        if value < 1024.0:
            break
    digits = 0 if value >= 100 else precision
    text = f"{value:.{digits}f}"
    return f"{text} {unit}"


def format_percent(value: float, *, precision: int = 0) -> str:
    """Format a percentage using a dot as the decimal separator."""
    text = f"{max(0.0, float(value)):.{precision}f}"
    return f"{text}%"


def format_count(value: int) -> str:
    """Format an integer using a comma as the thousands separator."""
    return f"{int(value):,}"


def clamp(value: float, minimum: float, maximum: float) -> float:
    """Clamp ``value`` to the ``minimum``..``maximum`` range."""
    return max(minimum, min(maximum, value))


def safe_ratio(part: float, total: float) -> float:
    """Return a 0..1 ratio without dividing by zero."""
    if total <= 0:
        return 0.0
    return clamp(part / total, 0.0, 1.0)


def human_duration(seconds: float) -> str:
    """Turn seconds into a short duration string in English."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} seconds"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minutes"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hours"
    days = hours // 24
    return f"{days} days"


def is_finite_number(value: object) -> bool:
    """True when ``value`` is a finite number (and not a bool)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))
