"""CLIF medication_admin_* -> FHIR MedicationAdministration.

CLIF carries no RxNorm column, so medications get CLIF-native codes with
med_name preserved as the display text.
"""
from typing import Any, Dict

from fhir.resources.R4B.medicationadministration import MedicationAdministration

from helios.fhir.codes.systems import clif_system
from helios.fhir.ids import make_id

# CLIF mar_action_category -> FHIR MedicationAdministration.status
_STATUS = {
    "start": "in-progress", "going": "in-progress", "dose_change": "in-progress",
    "stop": "completed", "given": "completed", "bolus": "completed",
    "not_given": "not-done",
}


def to_fhir(table: str, row: Dict[str, Any], patient_id: str) -> Dict[str, Any]:
    hosp_id = str(row["hospitalization_id"])
    when = row.get("admin_dttm")
    category = row.get("med_category")
    resource: Dict[str, Any] = {
        "resourceType": "MedicationAdministration",
        "id": make_id(table, row, hosp_id, when, category),
        "status": _STATUS.get(row.get("mar_action_category"), "unknown"),
        "medicationCodeableConcept": {
            "coding": [{"system": clif_system(table), "code": category or "unknown"}],
            "text": row.get("med_name") or category or "unknown",
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "context": {"reference": f"Encounter/{hosp_id}"},
    }
    if when is not None:
        resource["effectiveDateTime"] = when.isoformat()

    dosage: Dict[str, Any] = {}
    if row.get("med_route_category"):
        dosage["route"] = {"text": row["med_route_category"]}
    if row.get("med_dose") is not None:
        dosage["dose"] = {"value": float(row["med_dose"]),
                          "unit": row.get("med_dose_unit") or "1"}
    if dosage:
        resource["dosage"] = dosage

    MedicationAdministration(**resource)
    return resource
