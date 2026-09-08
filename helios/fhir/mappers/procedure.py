"""CLIF patient_procedures -> FHIR Procedure."""
from typing import Any, Dict

from fhir.resources.R4B.procedure import Procedure

from helios.fhir.codes.systems import code_system_uri
from helios.fhir.ids import make_id
from helios.fhir.times import fhir_datetime


def to_fhir(row: Dict[str, Any], patient_id: str) -> Dict[str, Any]:
    hosp_id = str(row["hospitalization_id"])
    when = row.get("procedure_billed_dttm")
    code = str(row["procedure_code"])
    resource: Dict[str, Any] = {
        "resourceType": "Procedure",
        "id": make_id("patient_procedures", row, hosp_id, when, code),
        "status": "completed",
        "code": {"coding": [{
            "system": code_system_uri(str(row.get("procedure_code_format", ""))),
            "code": code}]},
        "subject": {"reference": f"Patient/{patient_id}"},
        "encounter": {"reference": f"Encounter/{hosp_id}"},
    }
    if when is not None:
        resource["performedDateTime"] = fhir_datetime(when)
    Procedure(**resource)
    return resource
