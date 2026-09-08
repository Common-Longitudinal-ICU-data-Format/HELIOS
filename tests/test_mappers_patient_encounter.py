from datetime import datetime, timezone

from helios.fhir.mappers import encounter as em
from helios.fhir.mappers import patient as pm

PROW = {"patient_id": "P1", "race_category": "White",
        "ethnicity_category": "Non-Hispanic", "sex_category": "Female",
        "birth_date": datetime(2040, 6, 1, tzinfo=timezone.utc), "death_dttm": None}

EROW = {"hospitalization_id": "H1", "patient_id": "P1",
        "admission_dttm": datetime(2110, 1, 1, tzinfo=timezone.utc),
        "discharge_dttm": datetime(2110, 1, 5, tzinfo=timezone.utc),
        "admission_type_category": "ed", "discharge_category": "Home"}


def test_patient_identity_and_gender():
    r = pm.to_fhir(PROW)
    assert r["resourceType"] == "Patient"
    assert r["id"] == "P1"
    assert r["gender"] == "female"
    assert r["birthDate"] == "2040-06-01"


def test_patient_carries_us_core_race_extension():
    exts = {e["url"] for e in pm.to_fhir(PROW).get("extension", [])}
    assert "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race" in exts


def test_deceased_only_when_death_recorded():
    assert "deceasedDateTime" not in pm.to_fhir(PROW)
    dead = {**PROW, "death_dttm": datetime(2110, 1, 5, tzinfo=timezone.utc)}
    assert pm.to_fhir(dead)["deceasedDateTime"].startswith("2110-01-05")


def test_encounter_shape():
    r = em.to_fhir(EROW)
    assert r["resourceType"] == "Encounter"
    assert r["id"] == "H1"
    assert r["subject"]["reference"] == "Patient/P1"
    assert r["period"]["start"].startswith("2110-01-01")


def test_encounter_status_is_a_valid_code():
    """fhir.resources does NOT validate enums, so assert membership here."""
    valid = {"planned", "arrived", "triaged", "in-progress", "onleave",
             "finished", "cancelled", "entered-in-error", "unknown"}
    assert em.to_fhir(EROW)["status"] in valid
    still = {**EROW, "discharge_dttm": None}
    assert em.to_fhir(still)["status"] == "in-progress"


def test_patient_gender_is_a_valid_code():
    valid = {"male", "female", "other", "unknown"}
    for sex in ("Male", "Female", "Unknown", None):
        assert pm.to_fhir({**PROW, "sex_category": sex})["gender"] in valid
