"""One real hospitalization rendered to FHIR, checked in and diffed.

Regenerate deliberately:
    HELIOS_REGOLD=1 .venv/bin/python -m pytest tests/test_golden.py
"""
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from helios.fhir.app import create_app
from helios.fhir.config import load_config

GOLDEN = Path(__file__).parent / "golden" / "encounter_sample.json"


@pytest.fixture(scope="module")
def real():
    cfg = load_config()
    if not (cfg.prepared_directory / "encounter_index.parquet").exists():
        pytest.skip("run helios.scripts.prepare_data first")
    return cfg, TestClient(create_app(cfg, tokens_path=str(Path("tokens.yaml"))))


def test_golden_hospitalization(real):
    import duckdb
    cfg, client = real
    hosp = duckdb.connect().execute(
        f"SELECT hospitalization_id FROM "
        f"'{cfg.prepared_directory}/encounter_index.parquet' ORDER BY 1 LIMIT 1"
    ).fetchone()[0]
    body = client.get("/fhir/Observation",
                      params={"encounter": hosp, "_count": 15},
                      headers={"Authorization": "Bearer agent-readonly"}).json()
    actual = json.dumps(body, indent=2, sort_keys=True, default=str)
    if os.environ.get("HELIOS_REGOLD"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(actual)
        pytest.skip("golden file regenerated")
    assert actual == GOLDEN.read_text()
