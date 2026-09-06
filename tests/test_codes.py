import pytest

from helios.fhir.codes.systems import (
    LABS_LOINC, VITALS_LOINC, clif_system, code_system_uri, loinc_for)


def test_clif_system_uri_shape():
    assert clif_system("vitals") == (
        "http://clif-consortium.org/fhir/CodeSystem/vitals-category")


@pytest.mark.parametrize("fmt,uri", [
    ("ICD10CM", "http://hl7.org/fhir/sid/icd-10-cm"),
    ("ICD9CM", "http://hl7.org/fhir/sid/icd-9-cm"),
    ("ICD10PCS", "http://www.cms.gov/Medicare/Coding/ICD10"),
    ("CPT", "http://www.ama-assn.org/go/cpt"),
    ("HCPCS", "urn:oid:2.16.840.1.113883.6.285"),
])
def test_code_system_uris(fmt, uri):
    assert code_system_uri(fmt) == uri


def test_all_nine_vitals_categories_are_mapped():
    assert set(VITALS_LOINC) == {
        "dbp", "heart_rate", "height_cm", "map", "respiratory_rate",
        "sbp", "spo2", "temp_c", "weight_kg"}


def test_all_fortyfive_lab_categories_are_mapped():
    assert len(LABS_LOINC) == 45


def test_loinc_lookup_returns_code_display_unit():
    code, display, unit = loinc_for("vitals", "heart_rate")
    assert code == "8867-4"
    assert unit == "/min"


def test_unknown_category_returns_none():
    assert loinc_for("vitals", "not_a_vital") is None


def test_loinc_codes_are_well_formed():
    """LOINC is digits-dash-checkdigit. A typo here is silent otherwise."""
    import re
    for table in (VITALS_LOINC, LABS_LOINC):
        for category, (code, _display, _unit) in table.items():
            assert re.fullmatch(r"\d{1,5}-\d", code), (category, code)


def test_mapped_categories_match_the_real_data():
    """Guards against curating a code for a category this export lacks."""
    import duckdb
    from pathlib import Path
    labs = Path("data/prepared/clif_labs.parquet")
    if not labs.exists():
        pytest.skip("run prepare_data first")
    actual = {r[0] for r in duckdb.connect().execute(
        f"SELECT DISTINCT lab_category FROM '{labs}'").fetchall() if r[0]}
    assert actual == set(LABS_LOINC)
