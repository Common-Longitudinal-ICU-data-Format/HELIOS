"""Content-addressed resource ids.

(table, hospitalization_id, second, category) is NOT unique: 710,555 colliding
vitals rows and 36,992 lab rows were measured on this export. The trailing
digest of the full row is what disambiguates them, and it is safe because
full rows are unique after prepare_data's dedup pass.

FHIR ids allow only [A-Za-z0-9-.] and at most 64 characters, so CLIF's
underscore-bearing table names get short codes rather than being mangled.
"""
import hashlib
import re
from datetime import datetime
from typing import Any, Dict, Optional

# Bijective. Keeps ids FHIR-legal and short; see tests for the coverage check.
TABLE_CODE = {
    "patient": "pt",
    "hospitalization": "enc",
    "adt": "adt",
    "vitals": "vit",
    "labs": "lab",
    "patient_assessments": "asm",
    "respiratory_support": "rsp",
    "position": "pos",
    "medication_admin_continuous": "mdc",
    "medication_admin_intermittent": "mdi",
    "hospital_diagnosis": "dx",
    "patient_procedures": "prc",
}
CODE_TABLE = {code: table for table, code in TABLE_CODE.items()}

_SAFE = re.compile(r"[^A-Za-z0-9.]")


def _digest(row: Dict[str, Any]) -> str:
    payload = "|".join(f"{k}={row[k]!r}" for k in sorted(row))
    return hashlib.sha1(payload.encode()).hexdigest()[:8]


def make_id(table: str, row: Dict[str, Any], hosp_id: str,
            clock_value: Optional[datetime], category: Optional[str]) -> str:
    epoch = int(clock_value.timestamp()) if clock_value is not None else 0
    # Hyphen is the field separator, so it cannot survive inside a field.
    # The category segment is decorative: the digest is what identifies a row.
    return "-".join([
        TABLE_CODE[table],
        _SAFE.sub(".", str(hosp_id)),
        str(epoch),
        _SAFE.sub(".", category or ""),
        _digest(row),
    ])


def parse_id(rid: str) -> Dict[str, str]:
    """Fields never contain hyphens, so a plain 5-way split is unambiguous.

    `category` comes back dot-encoded, not as the original CLIF string. Callers
    re-find rows by table + hosp_id + epoch and then match the whole id.
    """
    parts = rid.split("-")
    if len(parts) != 5:
        raise ValueError(f"malformed resource id: {rid!r}")
    code, hosp_id, epoch, category, digest = parts
    if code not in CODE_TABLE:
        raise ValueError(f"unknown table code {code!r} in id {rid!r}")
    return {"table": CODE_TABLE[code], "hosp_id": hosp_id, "epoch": epoch,
            "category": category, "digest": digest}
