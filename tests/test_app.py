import duckdb
import pytest
from fastapi.testclient import TestClient

from helios.fhir.app import create_app
from helios.fhir.config import Config
from helios.scripts.prepare_data import prepare


@pytest.fixture
def cfg(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    out.mkdir()
    con = duckdb.connect()
    con.execute(f"""COPY (SELECT * FROM (VALUES
        ('H1','P1',TIMESTAMP '2110-01-01 00:00:00',TIMESTAMP '2110-01-05 00:00:00')
      ) t(hospitalization_id,patient_id,admission_dttm,discharge_dttm))
      TO '{src}/clif_hospitalization.parquet'""")
    con.execute(f"""COPY (SELECT * FROM (VALUES
        ('P1','White','Non-Hispanic','Female',TIMESTAMP '2040-06-01 00:00:00',NULL)
      ) t(patient_id,race_category,ethnicity_category,sex_category,birth_date,death_dttm))
      TO '{src}/clif_patient.parquet'""")
    con.execute(f"""COPY (SELECT * FROM (VALUES
        ('H1',TIMESTAMP '2110-01-01 06:00:00','heart_rate',80.0,'HR'),
        ('H1',TIMESTAMP '2110-01-03 06:00:00','heart_rate',95.0,'HR')
      ) t(hospitalization_id,recorded_dttm,vital_category,vital_value,vital_name))
      TO '{src}/clif_vitals.parquet'""")
    c = Config("test", src, out, "parquet", "UTC")
    prepare(c, tables=["hospitalization", "patient", "vitals"])
    return c


@pytest.fixture
def client(cfg, tmp_path):
    """Auth disabled: no tokens file at the given path."""
    return TestClient(create_app(cfg, tokens_path=str(tmp_path / "absent.yaml")))


@pytest.fixture
def secured(cfg, tmp_path):
    """Auth enabled with two tokens of differing scope."""
    p = tmp_path / "tokens.yaml"
    p.write_text(
        "tokens:\n"
        "  full: [\"system/Patient.rs\", \"system/Observation.rs\"]\n"
        "  thin: [\"system/Patient.rs\"]\n"
    )
    return TestClient(create_app(cfg, tokens_path=str(p)))


def test_capability_statement(client):
    r = client.get("/fhir/metadata")
    assert r.status_code == 200
    assert r.json()["resourceType"] == "CapabilityStatement"


def test_capability_advertises_no_write_interactions(client):
    for res in client.get("/fhir/metadata").json()["rest"][0]["resource"]:
        assert {i["code"] for i in res["interaction"]} <= {"read", "search-type"}


def test_read_patient(client):
    r = client.get("/fhir/Patient/P1")
    assert r.status_code == 200 and r.json()["gender"] == "female"


def test_read_encounter(client):
    assert client.get("/fhir/Encounter/H1").json()["id"] == "H1"


def test_unknown_patient_is_404_with_operation_outcome(client):
    r = client.get("/fhir/Patient/NOPE")
    assert r.status_code == 404
    assert r.json()["resourceType"] == "OperationOutcome"


def test_search_returns_a_searchset_bundle(client):
    body = client.get("/fhir/Observation", params={"patient": "P1"}).json()
    assert body["resourceType"] == "Bundle" and body["type"] == "searchset"
    assert body["total"] == 2


def test_search_without_context_is_400(client):
    r = client.get("/fhir/Observation")
    assert r.status_code == 400
    assert r.json()["issue"][0]["code"] == "required"


def test_as_of_header_excludes_later_rows(client):
    r = client.get("/fhir/Observation", params={"patient": "P1"},
                   headers={"X-As-Of": "2110-01-02T00:00:00Z"})
    assert r.json()["total"] == 1


def test_malformed_as_of_is_400(client):
    r = client.get("/fhir/Observation", params={"patient": "P1"},
                   headers={"X-As-Of": "yesterday"})
    assert r.status_code == 400


def test_no_matches_returns_empty_bundle_not_404(client):
    r = client.get("/fhir/Observation", params={"patient": "P1", "code": "nothing"})
    assert r.status_code == 200 and r.json()["total"] == 0


def test_unknown_resource_type_is_404(client):
    assert client.get("/fhir/Practitioner", params={"patient": "P1"}).status_code == 404


@pytest.mark.parametrize("verb", ["post", "put", "patch", "delete"])
def test_write_verbs_are_405(client, verb):
    assert getattr(client, verb)("/fhir/Observation").status_code == 405


def test_read_by_id_round_trips(client):
    entries = client.get("/fhir/Observation", params={"patient": "P1"}).json()["entry"]
    rid = entries[0]["resource"]["id"]
    r = client.get(f"/fhir/Observation/{rid}")
    assert r.status_code == 200 and r.json()["id"] == rid


def test_malformed_id_is_404(client):
    assert client.get("/fhir/Observation/garbage").status_code == 404


def test_discovery_document(client):
    body = client.get("/.well-known/smart-configuration").json()
    assert "token_endpoint" in body
    assert "client_credentials" in body["grant_types_supported"]


def test_secured_server_rejects_missing_token(secured):
    r = secured.get("/fhir/Observation", params={"patient": "P1"})
    assert r.status_code == 401
    assert r.json()["resourceType"] == "OperationOutcome"


def test_secured_server_rejects_unknown_token(secured):
    r = secured.get("/fhir/Observation", params={"patient": "P1"},
                    headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_secured_server_rejects_insufficient_scope(secured):
    r = secured.get("/fhir/Observation", params={"patient": "P1"},
                    headers={"Authorization": "Bearer thin"})
    assert r.status_code == 403


def test_secured_server_accepts_correct_scope(secured):
    r = secured.get("/fhir/Observation", params={"patient": "P1"},
                    headers={"Authorization": "Bearer full"})
    assert r.status_code == 200 and r.json()["total"] == 2


def test_scope_is_checked_per_resource_type(secured):
    """The thin token may read Patient but not Observation."""
    assert secured.get("/fhir/Patient/P1",
                       headers={"Authorization": "Bearer thin"}).status_code == 200
