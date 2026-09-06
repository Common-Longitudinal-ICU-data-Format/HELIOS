"""Timestamp handling.

CLIF parquet stores naive timestamps; FHIR dateTime requires an offset whenever
a time is present. The site timezone from config supplies it. Nothing here
shifts an instant - MIMIC dates pass through as stored (spec decision 6).

Naive values are also anchored to UTC before computing epochs for resource ids,
because datetime.timestamp() on a naive value uses the *machine's* local zone,
which would make ids differ between machines.
"""
from datetime import datetime, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

_SITE_TZ = timezone.utc


def configure(tz_name: str) -> None:
    """Set the offset used when serialising naive timestamps."""
    global _SITE_TZ
    if ZoneInfo is not None:
        try:
            _SITE_TZ = ZoneInfo(tz_name)
            return
        except Exception:
            pass
    _SITE_TZ = timezone.utc


def fhir_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=_SITE_TZ)
    return value.isoformat()


def epoch_seconds(value: Optional[datetime]) -> int:
    """Machine-independent: naive values are read as UTC, never as local time."""
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())
