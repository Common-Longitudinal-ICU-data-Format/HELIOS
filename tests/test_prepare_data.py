import duckdb
import pytest

from helios.fhir.config import Config
from helios.scripts.prepare_data import prepare


@pytest.fixture
def tiny(tmp_path):
    """Two hospitalizations out of order; position carries an exact duplicate."""
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    out.mkdir()
    con = duckdb.connect()
    con.execute(f"""COPY (SELECT * FROM (VALUES
        ('H2','P1',TIMESTAMP '2110-01-02 00:00:00',TIMESTAMP '2110-01-09 00:00:00'),
        ('H1','P1',TIMESTAMP '2110-01-01 00:00:00',TIMESTAMP '2110-01-05 00:00:00')
      ) t(hospitalization_id,patient_id,admission_dttm,discharge_dttm))
      TO '{src}/clif_hospitalization.parquet'""")
    con.execute(f"""COPY (SELECT * FROM (VALUES
        ('H1',TIMESTAMP '2110-01-01 05:00:00','prone','Prone'),
        ('H1',TIMESTAMP '2110-01-01 05:00:00','prone','Prone')
      ) t(hospitalization_id,recorded_dttm,position_category,position_name))
      TO '{src}/clif_position.parquet'""")
    return Config("test", src, out, "parquet", "UTC")


def test_dedups_only_flagged_tables(tiny):
    counts = prepare(tiny, tables=["hospitalization", "position"])
    assert counts["position"] == 1
    assert counts["hospitalization"] == 2


def test_output_is_sorted_by_hospitalization_id(tiny):
    prepare(tiny, tables=["hospitalization", "position"])
    ids = duckdb.connect().execute(
        f"SELECT hospitalization_id FROM "
        f"'{tiny.prepared_directory}/clif_hospitalization.parquet'"
    ).fetchall()
    assert [r[0] for r in ids] == ["H1", "H2"]


def test_builds_encounter_index(tiny):
    prepare(tiny, tables=["hospitalization", "position"])
    rows = duckdb.connect().execute(
        f"SELECT hospitalization_id, patient_id FROM "
        f"'{tiny.prepared_directory}/encounter_index.parquet' ORDER BY 1"
    ).fetchall()
    assert rows == [("H1", "P1"), ("H2", "P1")]


def test_unflagged_table_with_duplicates_fails_the_build(tiny):
    con = duckdb.connect()
    con.execute(f"""COPY (SELECT * FROM (VALUES
        ('H1',TIMESTAMP '2110-01-01 05:00:00','heart_rate',80.0,'HR'),
        ('H1',TIMESTAMP '2110-01-01 05:00:00','heart_rate',80.0,'HR')
      ) t(hospitalization_id,recorded_dttm,vital_category,vital_value,vital_name))
      TO '{tiny.data_directory}/clif_vitals.parquet'""")
    with pytest.raises(ValueError, match="duplicate"):
        prepare(tiny, tables=["vitals"])


def test_absent_table_is_skipped_not_an_error(tiny):
    counts = prepare(tiny, tables=["hospitalization", "labs"])
    assert "labs" not in counts
