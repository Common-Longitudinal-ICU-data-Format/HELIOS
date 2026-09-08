"""CLIF patient -> FHIR Patient."""
from typing import Any, Dict, Optional

from helios.fhir.times import fhir_datetime as _iso
from fhir.resources.R4B.patient import Patient

US_CORE_RACE = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race"
US_CORE_ETHNICITY = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity"

_GENDER = {"Male": "male", "Female": "female", "Unknown": "unknown"}




def to_fhir(row: Dict[str, Any]) -> Dict[str, Any]:
    resource: Dict[str, Any] = {
        "resourceType": "Patient",
        "id": str(row["patient_id"]),
        "gender": _GENDER.get(row.get("sex_category"), "unknown"),
    }
    birth = row.get("birth_date")
    if birth is not None:
        resource["birthDate"] = birth.date().isoformat()
    death = row.get("death_dttm")
    if death is not None:
        resource["deceasedDateTime"] = _iso(death)

    extensions = []
    if row.get("race_category"):
        extensions.append({"url": US_CORE_RACE, "valueString": row["race_category"]})
    if row.get("ethnicity_category"):
        extensions.append(
            {"url": US_CORE_ETHNICITY, "valueString": row["ethnicity_category"]})
    if extensions:
        resource["extension"] = extensions

    Patient(**resource)  # structural validation; raises on malformed
    return resource
