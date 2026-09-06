from datetime import datetime, timezone

from helios.fhir.mappers import observation as om

T = datetime(2121, 8, 31, 23, 0, tzinfo=timezone.utc)

ALINE = {"hospitalization_id": "H1", "recorded_dttm": T, "vital_category": "map",
         "vital_value": 75.0, "vital_name": "Arterial Blood Pressure mean"}
CUFF = {"hospitalization_id": "H1", "recorded_dttm": T, "vital_category": "map",
        "vital_value": 79.0, "vital_name": "Non Invasive Blood Pressure mean"}
LAB = {"hospitalization_id": "H1", "lab_result_dttm": T, "lab_category": "creatinine",
       "lab_value_numeric": 1.4, "lab_name": "Creatinine, Serum",
       "reference_unit": "mg/dL"}


def test_vitals_get_loinc():
    c = om.to_fhir("vitals", ALINE, "P1")["code"]["coding"][0]
    assert c["system"] == "http://loinc.org"
    assert c["code"] == "8478-0"


def test_measurement_method_is_preserved():
    """The whole reason *_name may not be dropped: A-line vs cuff."""
    a, c = om.to_fhir("vitals", ALINE, "P1"), om.to_fhir("vitals", CUFF, "P1")
    assert a["method"]["text"] == "Arterial Blood Pressure mean"
    assert c["method"]["text"] == "Non Invasive Blood Pressure mean"
    assert a["id"] != c["id"]
    assert a["valueQuantity"]["value"] == 75.0


def test_labs_get_loinc_and_unit_from_the_data():
    r = om.to_fhir("labs", LAB, "P1")
    assert r["code"]["coding"][0]["code"] == "2160-0"
    assert r["valueQuantity"]["unit"] == "mg/dL"


def test_unmapped_category_falls_back_to_clif_native():
    row = {**ALINE, "vital_category": "unmapped_thing"}
    c = om.to_fhir("vitals", row, "P1")["code"]["coding"][0]
    assert c["system"] == "http://clif-consortium.org/fhir/CodeSystem/vitals-category"
    assert c["code"] == "unmapped_thing"


def test_category_codes_are_valid_observation_categories():
    valid = {"vital-signs", "laboratory", "survey", "therapy", "activity"}
    for table in ("vitals", "labs", "patient_assessments",
                  "respiratory_support", "position"):
        assert om.CATEGORY_BY_TABLE[table] in valid


def test_status_is_always_final():
    assert om.to_fhir("vitals", ALINE, "P1")["status"] == "final"


def test_subject_and_encounter_references():
    r = om.to_fhir("vitals", ALINE, "P1")
    assert r["subject"]["reference"] == "Patient/P1"
    assert r["encounter"]["reference"] == "Encounter/H1"


def test_assessment_uses_the_populated_value_column():
    num = {"hospitalization_id": "H1", "recorded_dttm": T,
           "assessment_category": "gcs_total", "assessment_name": "GCS Total",
           "numerical_value": 14.0, "categorical_value": None, "text_value": None}
    cat = {**num, "numerical_value": None, "categorical_value": "RASS -2"}
    assert om.to_fhir("patient_assessments", num, "P1")["valueQuantity"]["value"] == 14.0
    assert om.to_fhir("patient_assessments", cat, "P1")["valueString"] == "RASS -2"


def test_respiratory_settings_become_components():
    row = {"hospitalization_id": "H1", "recorded_dttm": T,
           "device_category": "IMV", "device_name": "Ventilator",
           "fio2_set": 0.6, "peep_set": 8.0, "tidal_volume_set": None}
    comps = {c["code"]["text"]: c["valueQuantity"]["value"]
             for c in om.to_fhir("respiratory_support", row, "P1")["component"]}
    assert comps == {"fio2_set": 0.6, "peep_set": 8.0}
