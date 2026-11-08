"""Posted-time parsing, lookback windows, and seniority labels."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

LOOKBACK_MINUTES = (30, 60, 120, 180, 240, 360, 720, 1440)

_RELATIVE = re.compile(
    r"(?P<just>just now)|"
    r"(?P<yesterday>yesterday)|"
    r"(?:an?|1)\s+(?P<unit1>minute|hour|day|week|month)s?\s+ago|"
    r"(?P<n>\d+)\s*(?P<unit2>minutes?|hours?|days?|weeks?|months?|m|h|d)\s+ago",
    re.I,
)
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?")
_DATE = re.compile(
    r"(?:posted|updated)?\s*"
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+(\d{1,2})(?:,?\s+(\d{4}))?",
    re.I,
)
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_UNITS = {
    "m": "minutes", "minute": "minutes", "minutes": "minutes",
    "h": "hours", "hour": "hours", "hours": "hours",
    "d": "days", "day": "days", "days": "days",
    "week": "weeks", "weeks": "weeks",
    "month": "months", "months": "months",
}


def format_lookback(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h"


def coerce_lookback(minutes: int | str | None) -> int:
    try:
        value = int(minutes)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 60
    if value in LOOKBACK_MINUTES:
        return value
    return min(LOOKBACK_MINUTES, key=lambda choice: abs(choice - value))


def job_cap_for(minutes: int) -> int:
    if minutes <= 30:
        return 20
    if minutes <= 60:
        return 30
    if minutes <= 180:
        return 40
    if minutes <= 360:
        return 55
    return 80


def page_cap_for(minutes: int) -> int:
    if minutes <= 60:
        return 2
    if minutes <= 360:
        return 4
    return 6


def _to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_posted(text: str, *, now: datetime | None = None) -> tuple[str | None, str | None]:
    """Return (iso_utc, original snippet) from listing/job text."""
    if not text:
        return None, None
    now = now or datetime.now(timezone.utc)
    iso_match = _ISO.search(text)
    if iso_match and "T" in iso_match.group(0):
        raw = iso_match.group(0).replace(" ", "T")
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return _to_iso(dt), iso_match.group(0)
        except ValueError:
            pass
    rel = _RELATIVE.search(text)
    if rel:
        label = rel.group(0)
        if rel.group("just"):
            return _to_iso(now), label
        if rel.group("yesterday"):
            return _to_iso(now - timedelta(days=1)), label
        n = int(rel.group("n") or 1)
        unit = _UNITS[(rel.group("unit1") or rel.group("unit2") or "minutes").lower()]
        delta = {
            "minutes": timedelta(minutes=n),
            "hours": timedelta(hours=n),
            "days": timedelta(days=n),
            "weeks": timedelta(weeks=n),
            "months": timedelta(days=30 * n),
        }[unit]
        return _to_iso(now - delta), label
    if iso_match:
        try:
            dt = datetime.fromisoformat(iso_match.group(0) + "T12:00:00+00:00")
            return _to_iso(dt), iso_match.group(0)
        except ValueError:
            pass
    named = _DATE.search(text)
    if named:
        month = _MONTHS[named.group(1).lower()]
        day = int(named.group(2))
        year = int(named.group(3) or now.year)
        try:
            dt = datetime(year, month, day, 12, 0, tzinfo=timezone.utc)
            return _to_iso(dt), named.group(0)
        except ValueError:
            pass
    return None, None


def within_lookback(posted_iso: str | None, minutes: int, *, now: datetime | None = None) -> bool:
    """Unknown posted time is kept so a later wider pull can still fetch it."""
    if not posted_iso:
        return True
    now = now or datetime.now(timezone.utc)
    try:
        posted = datetime.fromisoformat(posted_iso.replace("Z", "+00:00"))
    except ValueError:
        return True
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return posted >= now - timedelta(minutes=minutes)


def classify_seniority(title: str, description: str = "") -> str:
    blob = f"{title} {description[:400]}".lower()
    if re.search(r"\b(intern|internship|co-?op|new grad|university|entry[ -]?level|junior|jr\.?)\b", blob):
        return "Entry"
    if re.search(r"\barchitect\b", title, re.I):
        return "Architect"
    if re.search(r"\b(principal|distinguished|fellow)\b", title, re.I):
        return "Principal"
    if re.search(r"\bstaff\b", title, re.I):
        return "Staff"
    if re.search(r"\b(director|head of|engineering manager|manager|lead)\b", title, re.I):
        return "Lead"
    if re.search(r"\b(senior|sr\.?)\b", title, re.I):
        return "Senior"
    if re.search(r"\b(mid[ -]?level|\bii\b|\b2\b)\b", title, re.I):
        return "Mid"
    return "Mid"
