"""CLIF hospital_diagnosis -> FHIR Condition.

This table has no timestamp column, so Condition cannot be as-of filtered and
carries no date. Do not synthesise one - see spec limitation 1.
"""
from typing import Any, Dict

from fhir.resources.R4B.condition import Condition

from helios.fhir.codes.systems import code_system_uri
from helios.fhir.ids import make_id

CONDITION_CLINICAL = "http://terminology.hl7.org/CodeSystem/condition-clinical"


def to_fhir(row: Dict[str, Any], patient_id: str) -> Dict[str, Any]:
    hosp_id = str(row["hospitalization_id"])
    code = str(row["diagnosis_code"])
    resource: Dict[str, Any] = {
        "resourceType": "Condition",
        "id": make_id("hospital_diagnosis", row, hosp_id, None, code),
        "clinicalStatus": {
            "coding": [{"system": CONDITION_CLINICAL, "code": "active"}]},
        "code": {"coding": [{
            "system": code_system_uri(str(row.get("diagnosis_code_format", ""))),
            "code": code}]},
        "subject": {"reference": f"Patient/{patient_id}"},
        "encounter": {"reference": f"Encounter/{hosp_id}"},
    }
    Condition(**resource)
    return resource
