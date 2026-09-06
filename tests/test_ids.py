import re
from datetime import datetime, timezone

from helios.fhir.ids import make_id, parse_id

T = datetime(2121, 8, 31, 23, 0, tzinfo=timezone.utc)

A = {"hospitalization_id": "20000147", "recorded_dttm": T, "vital_category": "map",
     "vital_value": 75.0, "vital_name": "Arterial Blood Pressure mean"}
B = {"hospitalization_id": "20000147", "recorded_dttm": T, "vital_category": "map",
     "vital_value": 79.0, "vital_name": "Non Invasive Blood Pressure mean"}


def test_same_key_different_values_get_different_ids():
    """The real collision case: A-line and cuff MAP at the same minute."""
    assert make_id("vitals", A, "20000147", T, "map") != \
        make_id("vitals", B, "20000147", T, "map")


def test_id_is_stable_across_calls():
    assert make_id("vitals", A, "20000147", T, "map") == \
        make_id("vitals", dict(A), "20000147", T, "map")


def test_id_is_parseable_back_to_its_parts():
    rid = make_id("vitals", A, "20000147", T, "map")
    parts = parse_id(rid)
    assert parts["table"] == "vitals"
    assert parts["hosp_id"] == "20000147"
    assert parts["category"] == "map"
    assert int(parts["epoch"]) == int(T.timestamp())


def test_id_is_fhir_legal():
    """FHIR ids: A-Za-z0-9-. only, max 64 chars."""
    rid = make_id("vitals", A, "20000147", T, "map")
    assert re.fullmatch(r"[A-Za-z0-9\-.]{1,64}", rid), rid


def test_category_is_optional():
    rid = make_id("hospital_diagnosis", {"diagnosis_code": "I10"}, "H1", None, None)
    assert parse_id(rid)["category"] == ""


def test_multiword_table_name_round_trips():
    rid = make_id("patient_assessments", A, "H1", T, "gcs_total")
    assert parse_id(rid)["table"] == "patient_assessments"


def test_table_codes_cover_every_in_scope_table_and_are_unique():
    from helios.fhir.ids import TABLE_CODE
    from helios.fhir.tables import IN_SCOPE
    assert set(TABLE_CODE) == set(IN_SCOPE)
    assert len(set(TABLE_CODE.values())) == len(TABLE_CODE)


def test_unknown_table_code_is_rejected():
    import pytest
    with pytest.raises(ValueError, match="unknown table code"):
        parse_id("zz-H1-0-x-abcdef12")


def test_every_table_produces_a_legal_id():
    from helios.fhir.ids import TABLE_CODE
    for table in TABLE_CODE:
        rid = make_id(table, A, "20000147", T, "some_long_category_name")
        assert re.fullmatch(r"[A-Za-z0-9\-.]{1,64}", rid), (table, rid, len(rid))


def test_ids_do_not_depend_on_the_machine_timezone():
    """datetime.timestamp() on a naive value uses local time; ids must not."""
    import os
    import time
    from datetime import datetime as dt
    naive = dt(2121, 8, 31, 23, 0)
    row = {**A, "recorded_dttm": naive}
    before = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "UTC"; time.tzset()
        utc_id = make_id("vitals", row, "H1", naive, "map")
        os.environ["TZ"] = "Asia/Kolkata"; time.tzset()
        ist_id = make_id("vitals", row, "H1", naive, "map")
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()
    assert utc_id == ist_id
