from pathlib import Path
import pytest
from helios.fhir.config import load_config, Config


def _write(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "site_name: mimic\n"
        f"data_directory: {tmp_path}\n"
        f"prepared_directory: {tmp_path}/prepared\n"
        "filetype: parquet\ntimezone: US/Eastern\n"
    )
    return p


def test_loads_config(tmp_path):
    c = load_config(str(_write(tmp_path)))
    assert isinstance(c, Config)
    assert c.site_name == "mimic"
    assert c.data_directory == Path(tmp_path)
    assert c.timezone == "US/Eastern"


def test_missing_key_is_an_error(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("site_name: mimic\n")
    with pytest.raises(KeyError):
        load_config(str(p))
