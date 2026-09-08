"""CLIF hospitalization -> FHIR Encounter."""
from typing import Any, Dict, Optional

from helios.fhir.times import fhir_datetime as _iso
from fhir.resources.R4B.encounter import Encounter

CLIF_ADMISSION = "http://clif-consortium.org/fhir/CodeSystem/hospitalization-category"
ACT_CODE = "http://terminology.hl7.org/CodeSystem/v3-ActCode"

# Every CLIF hospitalization is an inpatient stay. R4B requires Encounter.class.
INPATIENT = {"system": ACT_CODE, "code": "IMP", "display": "inpatient encounter"}




def to_fhir(row: Dict[str, Any]) -> Dict[str, Any]:
    discharged = row.get("discharge_dttm") is not None
    resource: Dict[str, Any] = {
        "resourceType": "Encounter",
        "id": str(row["hospitalization_id"]),
        "status": "finished" if discharged else "in-progress",
        "class": INPATIENT,
        "subject": {"reference": f"Patient/{row['patient_id']}"},
        "period": {"start": _iso(row.get("admission_dttm"))},
    }
    if discharged:
        resource["period"]["end"] = _iso(row["discharge_dttm"])
    if row.get("admission_type_category"):
        resource["type"] = [{"coding": [{
            "system": CLIF_ADMISSION,
            "code": row["admission_type_category"],
            "display": row.get("admission_type_name") or row["admission_type_category"],
        }]}]
    if row.get("discharge_category"):
        resource["hospitalization"] = {
            "dischargeDisposition": {"text": row["discharge_category"]}}

    Encounter(**resource)
    return resource
