"""Resource-type registry. Adding the microbiology tables later is an entry here."""
from typing import Any, Dict, List

from . import condition, encounter, medication, observation, patient, procedure

RESOURCE_TABLES: Dict[str, List[str]] = {
    "Patient": ["patient"],
    "Encounter": ["hospitalization", "adt"],
    "Observation": ["vitals", "labs", "patient_assessments",
                    "respiratory_support", "position"],
    "Condition": ["hospital_diagnosis"],
    "Procedure": ["patient_procedures"],
    "MedicationAdministration": ["medication_admin_continuous",
                                 "medication_admin_intermittent"],
}

TABLE_TO_RESOURCE = {t: r for r, ts in RESOURCE_TABLES.items() for t in ts}


def map_row(table: str, row: Dict[str, Any], patient_id: str) -> Dict[str, Any]:
    if table in observation.CATEGORY_BY_TABLE:
        return observation.to_fhir(table, row, patient_id)
    if table == "hospital_diagnosis":
        return condition.to_fhir(row, patient_id)
    if table == "patient_procedures":
        return procedure.to_fhir(row, patient_id)
    if table.startswith("medication_admin"):
        return medication.to_fhir(table, row, patient_id)
    if table == "patient":
        return patient.to_fhir(row)
    if table == "hospitalization":
        return encounter.to_fhir(row)
    raise KeyError(f"no mapper for table {table!r}")
