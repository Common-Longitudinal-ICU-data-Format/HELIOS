from datetime import datetime, timezone

import duckdb
import pytest

from helios.fhir.config import Config
from helios.fhir.store import Store
from helios.scripts.prepare_data import prepare


@pytest.fixture
def store(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    out.mkdir()
    con = duckdb.connect()
    con.execute(f"""COPY (SELECT * FROM (VALUES
        ('H1','P1',TIMESTAMP '2110-01-01 00:00:00',TIMESTAMP '2110-01-05 00:00:00'),
        ('H2','P1',TIMESTAMP '2110-02-01 00:00:00',TIMESTAMP '2110-02-03 00:00:00')
      ) t(hospitalization_id,patient_id,admission_dttm,discharge_dttm))
      TO '{src}/clif_hospitalization.parquet'""")
    con.execute(f"""COPY (SELECT * FROM (VALUES
        ('P1','White','Non-Hispanic','Female',TIMESTAMP '2040-06-01 00:00:00')
      ) t(patient_id,race_category,ethnicity_category,sex_category,birth_date))
      TO '{src}/clif_patient.parquet'""")
    con.execute(f"""COPY (SELECT * FROM (VALUES
        ('H1',TIMESTAMP '2110-01-01 06:00:00','heart_rate',80.0,'HR'),
        ('H1',TIMESTAMP '2110-01-03 06:00:00','heart_rate',95.0,'HR'),
        ('H2',TIMESTAMP '2110-02-01 06:00:00','heart_rate',70.0,'HR')
      ) t(hospitalization_id,recorded_dttm,vital_category,vital_value,vital_name))
      TO '{src}/clif_vitals.parquet'""")
    cfg = Config("test", src, out, "parquet", "UTC")
    prepare(cfg, tables=["hospitalization", "patient", "vitals"])
    return Store(cfg)


def test_encounter_lookup(store):
    e = store.encounter("H1")
    assert e["patient_id"] == "P1"
    assert e["admission_dttm"].year == 2110


def test_unknown_encounter_is_none(store):
    assert store.encounter("NOPE") is None


def test_encounters_for_patient(store):
    assert {e["hospitalization_id"]
            for e in store.encounters_for_patient("P1")} == {"H1", "H2"}


def test_patient_lookup(store):
    assert store.patient("P1")["sex_category"] == "Female"


def test_query_is_scoped_to_one_hospitalization(store):
    rows = store.query("vitals", "H1")
    assert len(rows) == 2
    assert all(r["hospitalization_id"] == "H1" for r in rows)


def test_as_of_excludes_later_rows(store):
    """The look-ahead test. This is the one that must never regress."""
    t = datetime(2110, 1, 2, 0, 0, tzinfo=timezone.utc)
    rows = store.query("vitals", "H1", as_of=t)
    assert len(rows) == 1
    assert rows[0]["vital_value"] == 80.0


def test_extra_predicates_are_applied(store):
    rows = store.query("vitals", "H1", predicates=["vital_category = 'heart_rate'"])
    assert len(rows) == 2
    assert store.query("vitals", "H1", predicates=["vital_category = 'sbp'"]) == []


def test_limit_is_respected(store):
    assert len(store.query("vitals", "H1", limit=1)) == 1


def test_absent_table_returns_empty(store):
    assert store.query("labs", "H1") == []


def test_timestamps_do_not_depend_on_the_machine_timezone(tmp_path):
    """DuckDB renders TIMESTAMPTZ in its session zone; pin it from config."""
    import os
    import time
    src, out = tmp_path / "s2", tmp_path / "o2"
    src.mkdir()
    out.mkdir()
    duckdb.connect().execute(f"""COPY (SELECT * FROM (VALUES
        ('H1','P1',TIMESTAMPTZ '2110-01-01 12:00:00+00',TIMESTAMPTZ '2110-01-05 12:00:00+00')
      ) t(hospitalization_id,patient_id,admission_dttm,discharge_dttm))
      TO '{src}/clif_hospitalization.parquet'""")
    cfg = Config("test", src, out, "parquet", "US/Eastern")
    prepare(cfg, tables=["hospitalization"])

    def offset():
        return Store(cfg).encounter("H1")["admission_dttm"].utcoffset()

    before = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "UTC"; time.tzset()
        a = offset()
        os.environ["TZ"] = "Asia/Kolkata"; time.tzset()
        b = offset()
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()
    assert a == b
