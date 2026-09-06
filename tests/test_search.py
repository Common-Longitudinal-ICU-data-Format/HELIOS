import pytest

from helios.fhir import search
from helios.fhir.outcome import HttpProblem


class FakeStore:
    def encounter(self, h):
        return {"hospitalization_id": "H1", "patient_id": "P1"} if h == "H1" else None

    def encounters_for_patient(self, p):
        if p != "P1":
            return []
        return [{"hospitalization_id": "H1"}, {"hospitalization_id": "H2"}]


def test_patient_param_resolves_to_all_encounters():
    pid, hosps = search.resolve_context(FakeStore(), {"patient": "P1"})
    assert pid == "P1" and hosps == ["H1", "H2"]


def test_encounter_param_resolves_to_one():
    pid, hosps = search.resolve_context(FakeStore(), {"encounter": "H1"})
    assert pid == "P1" and hosps == ["H1"]


def test_patient_reference_form_is_accepted():
    pid, _ = search.resolve_context(FakeStore(), {"patient": "Patient/P1"})
    assert pid == "P1"


def test_missing_context_is_a_400():
    with pytest.raises(HttpProblem) as e:
        search.resolve_context(FakeStore(), {"code": "creatinine"})
    assert e.value.status == 400
    assert e.value.code == "required"


def test_unknown_patient_yields_no_encounters():
    _pid, hosps = search.resolve_context(FakeStore(), {"patient": "NOPE"})
    assert hosps == []


def test_code_becomes_a_category_predicate():
    assert search.predicates_for("labs", {"code": "creatinine"}) == \
        ["lab_category = 'creatinine'"]


def test_date_ge_and_le():
    preds = search.predicates_for("vitals", {"date": ["ge2110-01-01", "le2110-01-05"]})
    assert any(">=" in p for p in preds) and any("<=" in p for p in preds)


def test_sql_quotes_are_escaped():
    p = search.predicates_for("labs", {"code": "bob's"})[0]
    assert "bob''s" in p


def test_table_without_category_ignores_code():
    assert search.predicates_for("hospital_diagnosis", {"code": "I10"}) == []
