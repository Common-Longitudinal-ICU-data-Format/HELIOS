from datetime import datetime, timezone

from helios.fhir.mappers import RESOURCE_TABLES, map_row
from helios.fhir.mappers import condition as cm
from helios.fhir.mappers import medication as mm
from helios.fhir.mappers import procedure as pm

T = datetime(2110, 3, 15, 8, 0, tzinfo=timezone.utc)

DX = {"hospitalization_id": "H1", "diagnosis_code": "I10",
      "diagnosis_code_format": "ICD10CM", "diagnosis_primary": 1, "poa_present": 1}
PR = {"hospitalization_id": "H1", "procedure_code": "0BH17EZ",
      "procedure_code_format": "ICD10PCS", "procedure_billed_dttm": T}
MED = {"hospitalization_id": "H1", "admin_dttm": T, "med_category": "norepinephrine",
       "med_name": "Norepinephrine Bitartrate", "med_dose": 0.1,
       "med_dose_unit": "mcg/kg/min", "med_route_category": "IV",
       "mar_action_category": "start"}


def test_condition_uses_icd10cm_system():
    r = cm.to_fhir(DX, "P1")
    assert r["resourceType"] == "Condition"
    assert r["code"]["coding"][0]["system"] == "http://hl7.org/fhir/sid/icd-10-cm"
    assert r["code"]["coding"][0]["code"] == "I10"


def test_condition_uses_clinical_status_not_status():
    """FHIR Condition has clinicalStatus; a bare `status` would be non-conformant."""
    r = cm.to_fhir(DX, "P1")
    assert "status" not in r
    valid = {"active", "recurrence", "relapse", "inactive", "remission", "resolved"}
    assert r["clinicalStatus"]["coding"][0]["code"] in valid


def test_condition_has_no_timestamp():
    """hospital_diagnosis carries no date - do not invent one."""
    r = cm.to_fhir(DX, "P1")
    assert "onsetDateTime" not in r and "recordedDate" not in r


def test_procedure_uses_icd10pcs_system():
    r = pm.to_fhir(PR, "P1")
    assert r["code"]["coding"][0]["system"] == "http://www.cms.gov/Medicare/Coding/ICD10"
    assert r["performedDateTime"].startswith("2110-03-15")


def test_procedure_status_is_valid():
    valid = {"preparation", "in-progress", "not-done", "on-hold", "stopped",
             "completed", "entered-in-error", "unknown"}
    assert pm.to_fhir(PR, "P1")["status"] in valid


def test_medication_dose_and_route():
    d = mm.to_fhir("medication_admin_continuous", MED, "P1")["dosage"]
    assert d["dose"]["value"] == 0.1 and d["dose"]["unit"] == "mcg/kg/min"
    assert d["route"]["text"] == "IV"


def test_medication_preserves_med_name():
    r = mm.to_fhir("medication_admin_continuous", MED, "P1")
    assert r["resourceType"] == "MedicationAdministration"
    assert r["medicationCodeableConcept"]["text"] == "Norepinephrine Bitartrate"


def test_medication_status_is_valid():
    valid = {"in-progress", "not-done", "on-hold", "completed",
             "entered-in-error", "stopped", "unknown"}
    for action in ("start", "going", "stop", "given", "not_given", None, "weird"):
        r = mm.to_fhir("medication_admin_continuous",
                       {**MED, "mar_action_category": action}, "P1")
        assert r["status"] in valid


def test_registry_covers_every_in_scope_table():
    from helios.fhir.tables import IN_SCOPE
    mapped = {t for tables in RESOURCE_TABLES.values() for t in tables}
    assert mapped == set(IN_SCOPE)


def test_map_row_dispatches_by_table():
    assert map_row("hospital_diagnosis", DX, "P1")["resourceType"] == "Condition"
    assert map_row("patient_procedures", PR, "P1")["resourceType"] == "Procedure"
    assert map_row("medication_admin_intermittent", MED, "P1")["resourceType"] == \
        "MedicationAdministration"
