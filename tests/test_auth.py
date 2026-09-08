import pytest

from helios.fhir.auth import load_tokens, require_scope, scope_for
from helios.fhir.outcome import HttpProblem

TOKENS = {"good": ["system/Patient.rs", "system/Observation.rs"],
          "narrow": ["system/Patient.rs"],
          "wild": ["system/*.rs"]}


def test_scope_name_for_resource():
    assert scope_for("Observation") == "system/Observation.rs"


def test_missing_header_is_401():
    with pytest.raises(HttpProblem) as e:
        require_scope(TOKENS, None, "Observation")
    assert e.value.status == 401


def test_unknown_token_is_401():
    with pytest.raises(HttpProblem) as e:
        require_scope(TOKENS, "Bearer nope", "Observation")
    assert e.value.status == 401


def test_non_bearer_scheme_is_401():
    with pytest.raises(HttpProblem) as e:
        require_scope(TOKENS, "Basic good", "Observation")
    assert e.value.status == 401


def test_valid_token_missing_scope_is_403():
    with pytest.raises(HttpProblem) as e:
        require_scope(TOKENS, "Bearer narrow", "Observation")
    assert e.value.status == 403


def test_valid_token_with_scope_passes():
    require_scope(TOKENS, "Bearer good", "Observation")


def test_wildcard_scope_passes():
    require_scope(TOKENS, "Bearer wild", "Observation")


def test_no_tokens_file_disables_auth():
    require_scope({}, None, "Observation")


def test_loads_tokens_from_yaml(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text('tokens:\n  a: ["system/Patient.rs"]\n')
    assert load_tokens(str(p)) == {"a": ["system/Patient.rs"]}


def test_missing_file_yields_no_tokens(tmp_path):
    assert load_tokens(str(tmp_path / "absent.yaml")) == {}
