"""FHIR search parameters -> SQL predicates.

Every search must name a patient or encounter. Without one there is nothing to
prune on and the query degrades to a full scan - which is also why Epic
mandates `patient` on nearly every search.
"""
from typing import Any, Dict, List, Tuple

from helios.fhir.outcome import HttpProblem
from helios.fhir.tables import TABLES

_DATE_OPS = {"ge": ">=", "le": "<=", "gt": ">", "lt": "<"}


def _q(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def resolve_context(store, params: Dict[str, Any]) -> Tuple[str, List[str]]:
    encounter_id = params.get("encounter")
    if encounter_id:
        row = store.encounter(str(encounter_id).replace("Encounter/", ""))
        if row is None:
            return "", []
        return row["patient_id"], [row["hospitalization_id"]]

    patient_id = params.get("patient") or params.get("subject")
    if patient_id:
        patient_id = str(patient_id).replace("Patient/", "")
        return patient_id, [e["hospitalization_id"]
                            for e in store.encounters_for_patient(patient_id)]

    raise HttpProblem(400, "required",
                      "a 'patient' or 'encounter' parameter is required")


def predicates_for(table: str, params: Dict[str, Any]) -> List[str]:
    spec = TABLES[table]
    out: List[str] = []

    code = params.get("code")
    if code and spec.category_column:
        out.append(f"{spec.category_column} = {_q(code)}")

    dates = params.get("date")
    if dates and spec.clock_column:
        for token in ([dates] if isinstance(dates, str) else dates):
            op = _DATE_OPS.get(token[:2])
            if op:
                out.append(f"{spec.clock_column} {op} TIMESTAMP {_q(token[2:])}")
            else:
                out.append(f"CAST({spec.clock_column} AS DATE) = DATE {_q(token)}")
    return out
