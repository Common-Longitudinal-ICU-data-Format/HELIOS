"""Runtime configuration. Mirrors the clifpy config shape already in use."""
from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass(frozen=True)
class Config:
    site_name: str
    data_directory: Path
    prepared_directory: Path
    filetype: str
    timezone: str


def load_config(path: str = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    return Config(
        site_name=raw["site_name"],
        data_directory=Path(raw["data_directory"]),
        prepared_directory=Path(raw["prepared_directory"]),
        filetype=raw["filetype"],
        timezone=raw["timezone"],
    )
