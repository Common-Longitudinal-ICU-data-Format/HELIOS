from helios.fhir.tables import TABLES, IN_SCOPE


def test_twelve_tables_in_scope():
    assert len(IN_SCOPE) == 12
    for excluded in ("crrt_therapy", "ecmo_mcs", "code_status"):
        assert excluded not in IN_SCOPE


def test_clock_columns_match_spec():
    assert TABLES["vitals"].clock_column == "recorded_dttm"
    assert TABLES["labs"].clock_column == "lab_result_dttm"
    assert TABLES["medication_admin_continuous"].clock_column == "admin_dttm"
    assert TABLES["adt"].clock_column == "in_dttm"
    assert TABLES["patient_procedures"].clock_column == "procedure_billed_dttm"
    assert TABLES["hospitalization"].clock_column == "admission_dttm"
    assert TABLES["hospital_diagnosis"].clock_column is None


def test_dedup_flags_match_measured_duplicates():
    assert {t for t in IN_SCOPE if TABLES[t].dedup} == {
        "patient_procedures", "hospital_diagnosis", "position"
    }


def test_name_column_always_paired_with_category():
    for t in IN_SCOPE:
        spec = TABLES[t]
        if spec.category_column is not None:
            assert spec.name_column is not None, f"{t} would drop *_name"
