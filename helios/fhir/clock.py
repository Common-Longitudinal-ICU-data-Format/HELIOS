"""The as-of clock: an agent must not see data recorded after the instant it
is reasoning at. Dates are native MIMIC time - nothing here converts them.
"""
from datetime import datetime
from typing import Optional

from helios.fhir.tables import TABLES


def parse_as_of(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"X-As-Of is not an ISO-8601 instant: {value!r}") from exc


def clock_predicate(table: str, as_of: Optional[datetime]) -> str:
    """SQL fragment, or "" when this table cannot be time-filtered."""
    if as_of is None:
        return ""
    column = TABLES[table].clock_column
    if column is None:
        return ""  # no timestamp on this table; see spec limitation 1
    return f"{column} <= TIMESTAMP '{as_of.strftime('%Y-%m-%d %H:%M:%S')}'"
