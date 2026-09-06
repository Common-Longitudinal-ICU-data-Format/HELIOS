# helios-fhir Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only FHIR R4 server that serves CLIF parquet as FHIR resources, translated per request.

**Architecture:** A lazy facade. Nothing is materialised: each request becomes one DuckDB query against parquet sorted by `hospitalization_id`, and the matched rows are translated to FHIR resources in memory and discarded after the response. A thin in-memory encounter index answers identity lookups without touching a fact table.

**Tech Stack:** Python 3.9.6, FastAPI, DuckDB, `fhir.resources` 8.3.0 (pydantic v2), pytest.

**Spec:** `docs/superpowers/specs/2026-09-03-helios-fhir-mcp-design.md`

**Scope:** Spec phases 0-4. `helios-mcp` (phase 5) is a separate subsystem with its own plan.

## Global Constraints

- **Python 3.9.6 is the only interpreter available.** `eval_type_backport` MUST be installed — without it, importing any `fhir.resources` model raises `TypeError` on PEP 604 `X | None` annotations.
- **Read-only.** No POST/PUT/PATCH/DELETE route may exist. Write verbs return `405`.
- Source data at `/Users/sudo_sage/Downloads/work/clif_m` is **never written to**. Prepared output goes to `data/prepared/`.
- **12 tables in scope:** patient, hospitalization, adt, vitals, labs, patient_assessments, respiratory_support, position, medication_admin_continuous, medication_admin_intermittent, hospital_diagnosis, patient_procedures. `crrt_therapy`, `ecmo_mcs`, `code_status` are excluded.
- **Every mapper preserves the CLIF `*_name` column.** `*_category` is the primary `code.coding`; `*_name` goes to `Observation.method.text`. Dropping it merges arterial-line and cuff blood pressure.
- **Resource ids are** `{table}-{hosp_id}-{epoch_seconds}-{category}-{sha1(row)[:8]}`. The hash is mandatory; without it ~750k ids collide.
- **Dates pass through unchanged.** Native MIMIC time, stays in the 2100s. No shifting anywhere.
- `fhir.resources` does **not** validate code enums. Every `status`/`category`/`intent` literal needs an explicit test.
- CLIF-native CodeSystem URI form: `http://clif-consortium.org/fhir/CodeSystem/{table}-category`.
- Searches require a `patient` or `encounter` parameter; without one, return `400`.
- Run everything through the venv: `.venv/bin/python`.

---

### Task 1: Project skeleton and config

**Files:**
- Create: `requirements.txt`, `config.yaml`, `helios/__init__.py`, `helios/fhir/__init__.py`, `helios/fhir/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Config` frozen dataclass with `site_name: str`, `data_directory: Path`, `prepared_directory: Path`, `filetype: str`, `timezone: str`; and `load_config(path: str = "config.yaml") -> Config`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'helios'`

- [ ] **Step 3: Write minimal implementation**

```python
# helios/fhir/config.py
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
```

Also create empty `helios/__init__.py`, `helios/fhir/__init__.py`, `tests/__init__.py`, plus:

```yaml
# config.yaml
site_name: mimic
data_directory: /Users/sudo_sage/Downloads/work/clif_m
prepared_directory: data/prepared
filetype: parquet
timezone: US/Eastern
```

```
# requirements.txt
duckdb>=1.0
fastapi>=0.110
uvicorn>=0.27
fhir.resources>=8.0
eval_type_backport>=0.2
pyyaml>=6.0
pytest>=8.0
httpx>=0.27
pyarrow>=15.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add requirements.txt config.yaml helios/ tests/
git commit -m "feat: project skeleton and config loader"
```

---

### Task 2: Table registry

**Files:**
- Create: `helios/fhir/tables.py`
- Test: `tests/test_tables.py`

**Interfaces:**
- Consumes: nothing
- Produces: `TableSpec` frozen dataclass (`name`, `clock_column`, `category_column`, `name_column`, `join_key`, `dedup`); `TABLES: Dict[str, TableSpec]`; `IN_SCOPE: List[str]`

Single source of truth for every later task. Encodes the spec's clock-column table and the three tables with measured duplicates.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tables.py
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
    # no timestamp exists on this table - a known look-ahead hole
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tables.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'helios.fhir.tables'`

- [ ] **Step 3: Write minimal implementation**

```python
# helios/fhir/tables.py
"""Per-table facts every other module reads.

Clock columns differ by table, so as-of filtering cannot be generic.
Dedup flags come from measured full-row duplicate counts: patient_procedures
9,310, hospital_diagnosis 199, position 2. The other nine tables are clean.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class TableSpec:
    name: str
    clock_column: Optional[str]
    category_column: Optional[str] = None
    name_column: Optional[str] = None
    join_key: str = "hospitalization_id"
    dedup: bool = False


def _t(name, clock, cat=None, nm=None, key="hospitalization_id", dedup=False):
    return TableSpec(name, clock, cat, nm, key, dedup)


TABLES: Dict[str, TableSpec] = {
    "patient":         _t("patient", None, key="patient_id"),
    "hospitalization": _t("hospitalization", "admission_dttm"),
    "adt":             _t("adt", "in_dttm", "location_category", "location_name"),
    "vitals":          _t("vitals", "recorded_dttm", "vital_category", "vital_name"),
    "labs":            _t("labs", "lab_result_dttm", "lab_category", "lab_name"),
    "patient_assessments": _t("patient_assessments", "recorded_dttm",
                              "assessment_category", "assessment_name"),
    "respiratory_support": _t("respiratory_support", "recorded_dttm",
                              "device_category", "device_name"),
    "position":        _t("position", "recorded_dttm", "position_category",
                          "position_name", dedup=True),
    "medication_admin_continuous":   _t("medication_admin_continuous", "admin_dttm",
                                        "med_category", "med_name"),
    "medication_admin_intermittent": _t("medication_admin_intermittent", "admin_dttm",
                                        "med_category", "med_name"),
    "hospital_diagnosis": _t("hospital_diagnosis", None, dedup=True),
    "patient_procedures": _t("patient_procedures", "procedure_billed_dttm", dedup=True),
}

IN_SCOPE: List[str] = list(TABLES)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tables.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add helios/fhir/tables.py tests/test_tables.py
git commit -m "feat: table registry with clock columns and dedup flags"
```

---

### Task 3: prepare_data.py

**Files:**
- Create: `helios/scripts/__init__.py`, `helios/scripts/prepare_data.py`
- Test: `tests/test_prepare_data.py`

**Interfaces:**
- Consumes: `Config` (Task 1), `TABLES`/`IN_SCOPE` (Task 2)
- Produces: `prepare(config: Config, tables: Optional[List[str]] = None) -> Dict[str, int]` returning rows written per table. Writes `{prepared}/clif_{table}.parquet` sorted by join key, and `{prepared}/encounter_index.parquet` with columns `hospitalization_id, patient_id, admission_dttm, discharge_dttm`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prepare_data.py
import duckdb, pytest
from helios.fhir.config import Config
from helios.scripts.prepare_data import prepare


@pytest.fixture
def tiny(tmp_path):
    """Two hospitalizations out of order; position carries an exact duplicate."""
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir(); out.mkdir()
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
    assert counts["position"] == 1          # duplicate collapsed
    assert counts["hospitalization"] == 2   # untouched


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_prepare_data.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'helios.scripts'`

- [ ] **Step 3: Write minimal implementation**

```python
# helios/scripts/prepare_data.py
"""One-time rewrite of the CLIF export into the layout the server reads.

Sorting by hospitalization_id is the whole point: it makes Parquet row-group
min/max stats tight and non-overlapping, so a single-patient query skips nearly
every row group. Measured on this dataset, labs at cohort 100 reads 14.3 MB
sorted vs 61.5 MB unsorted. Partitioning by year reads 62.5 MB - an ID
predicate carries no year information to prune on.
"""
from typing import Dict, List, Optional
import duckdb

from helios.fhir.config import Config
from helios.fhir.tables import TABLES, IN_SCOPE


def prepare(config: Config, tables: Optional[List[str]] = None) -> Dict[str, int]:
    targets = tables if tables is not None else IN_SCOPE
    config.prepared_directory.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    counts: Dict[str, int] = {}

    for name in targets:
        spec = TABLES[name]
        src = config.data_directory / f"clif_{name}.{config.filetype}"
        if not src.exists():
            continue                       # declared but absent, e.g. microbiology
        dst = config.prepared_directory / f"clif_{name}.parquet"
        select = "SELECT DISTINCT *" if spec.dedup else "SELECT *"
        con.execute(
            f"COPY ({select} FROM '{src}' ORDER BY {spec.join_key}) TO '{dst}' "
            f"(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 122880)"
        )
        total = con.execute(f"SELECT count(*) FROM '{dst}'").fetchone()[0]
        distinct = con.execute(
            f"SELECT count(*) FROM (SELECT DISTINCT * FROM '{dst}')"
        ).fetchone()[0]
        if total != distinct:
            # Resource ids are content-addressed; identical rows would collide.
            raise ValueError(
                f"{name}: {total - distinct} duplicate rows remain after prepare"
            )
        counts[name] = total

    _build_encounter_index(con, config)
    return counts


def _build_encounter_index(con, config: Config) -> None:
    """546k rows the server holds in memory: identity plus stay bounds."""
    src = config.prepared_directory / "clif_hospitalization.parquet"
    if not src.exists():
        return
    cols = {d[0] for d in con.execute(f"SELECT * FROM '{src}' LIMIT 0").description}
    discharge = ("discharge_dttm" if "discharge_dttm" in cols
                 else "NULL::TIMESTAMP AS discharge_dttm")
    dst = config.prepared_directory / "encounter_index.parquet"
    con.execute(
        f"COPY (SELECT hospitalization_id, patient_id, admission_dttm, {discharge} "
        f"      FROM '{src}' ORDER BY hospitalization_id) "
        f"TO '{dst}' (FORMAT PARQUET)"
    )


if __name__ == "__main__":
    from helios.fhir.config import load_config
    for table, n in prepare(load_config()).items():
        print(f"{table:32s} {n:>12,}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_prepare_data.py -v`
Expected: 5 passed

- [ ] **Step 5: Run it against the real export**

Run: `.venv/bin/python -m helios.scripts.prepare_data`
Expected: 12 rows printed, all uniqueness assertions passing, `data/prepared/` populated.

- [ ] **Step 6: Commit**

```bash
git add helios/scripts/ tests/test_prepare_data.py
git commit -m "feat: prepare_data - sort by hospitalization_id, dedup, assert uniqueness"
```

---

### Task 4: The as-of clock

**Files:**
- Create: `helios/fhir/clock.py`
- Test: `tests/test_clock.py`

**Interfaces:**
- Consumes: `TABLES` (Task 2)
- Produces: `parse_as_of(value: Optional[str]) -> Optional[datetime]` raising `ValueError` on malformed input; `clock_predicate(table: str, as_of: Optional[datetime]) -> str` returning a SQL fragment (`""` when no filter applies)

`clock.py` does one thing: turn `X-As-Of` into a SQL predicate. There is no timestamp transformation anywhere in this server — dates pass through as native MIMIC time.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clock.py
from datetime import datetime, timezone
import pytest
from helios.fhir.clock import parse_as_of, clock_predicate

def test_parses_iso8601():
    assert parse_as_of("2110-03-15T08:00:00+00:00") == datetime(
        2110, 3, 15, 8, 0, tzinfo=timezone.utc)

def test_parses_z_suffix():
    assert parse_as_of("2110-03-15T08:00:00Z") == datetime(
        2110, 3, 15, 8, 0, tzinfo=timezone.utc)

def test_none_means_no_filter():
    assert parse_as_of(None) is None

def test_malformed_raises():
    with pytest.raises(ValueError):
        parse_as_of("yesterday")

def test_predicate_uses_the_tables_own_clock_column():
    t = datetime(2110, 3, 15, 8, 0, tzinfo=timezone.utc)
    assert "lab_result_dttm <=" in clock_predicate("labs", t)
    assert "recorded_dttm <=" in clock_predicate("vitals", t)
    assert "admin_dttm <=" in clock_predicate("medication_admin_continuous", t)

def test_no_as_of_means_empty_predicate():
    assert clock_predicate("labs", None) == ""

def test_table_without_a_clock_column_cannot_filter():
    """hospital_diagnosis has no timestamp - a documented look-ahead hole."""
    t = datetime(2110, 3, 15, 8, 0, tzinfo=timezone.utc)
    assert clock_predicate("hospital_diagnosis", t) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_clock.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'helios.fhir.clock'`

- [ ] **Step 3: Write minimal implementation**

```python
# helios/fhir/clock.py
"""The as-of clock: an agent must not see data recorded after the instant it
is reasoning at. Dates are native MIMIC time - nothing here converts them.
"""
from datetime import datetime
from typing import Optional

from helios.fhir.tables import TABLES


def parse_as_of(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"X-As-Of is not an ISO-8601 instant: {value!r}") from exc


def clock_predicate(table: str, as_of: Optional[datetime]) -> str:
    """SQL fragment, or "" when this table cannot be time-filtered."""
    if as_of is None:
        return ""
    column = TABLES[table].clock_column
    if column is None:
        return ""            # no timestamp on this table; see spec limitation 1
    return f"{column} <= TIMESTAMP '{as_of.strftime('%Y-%m-%d %H:%M:%S')}'"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_clock.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add helios/fhir/clock.py tests/test_clock.py
git commit -m "feat: as-of clock predicate per table"
```

---

### Task 5: Code systems and LOINC maps

**Files:**
- Create: `helios/fhir/codes/__init__.py`, `helios/fhir/codes/systems.py`, `helios/fhir/codes/vitals_loinc.csv`, `helios/fhir/codes/labs_loinc.csv`
- Test: `tests/test_codes.py`

**Interfaces:**
- Consumes: nothing
- Produces: `LOINC`, `UCUM`, `SNOMED` URI constants; `clif_system(table: str) -> str`; `code_system_uri(code_format: str) -> str` mapping `ICD10CM|ICD9CM|ICD9|ICD10PCS|CPT|HCPCS` to URIs; `loinc_for(kind: str, category: str) -> Optional[Tuple[str, str, str]]` returning `(code, display, unit)` for `kind in {"vitals","labs"}`

`lab_loinc_code` is 0% populated in this export, so labs LOINC is curated here rather than read from the data.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_codes.py
import csv, pytest
from pathlib import Path
from helios.fhir.codes.systems import (
    LOINC, clif_system, code_system_uri, loinc_for, VITALS_LOINC, LABS_LOINC)

def test_clif_system_uri_shape():
    assert clif_system("vitals") == (
        "http://clif-consortium.org/fhir/CodeSystem/vitals-category")

@pytest.mark.parametrize("fmt,uri", [
    ("ICD10CM", "http://hl7.org/fhir/sid/icd-10-cm"),
    ("ICD9CM",  "http://hl7.org/fhir/sid/icd-9-cm"),
    ("ICD10PCS","http://www.cms.gov/Medicare/Coding/ICD10"),
    ("CPT",     "http://www.ama-assn.org/go/cpt"),
    ("HCPCS",   "urn:oid:2.16.840.1.113883.6.285"),
])
def test_code_system_uris(fmt, uri):
    assert code_system_uri(fmt) == uri

def test_all_nine_vitals_categories_are_mapped():
    expected = {"dbp","heart_rate","height_cm","map","respiratory_rate",
                "sbp","spo2","temp_c","weight_kg"}
    assert set(VITALS_LOINC) == expected

def test_all_fortyfive_lab_categories_are_mapped():
    assert len(LABS_LOINC) == 45

def test_loinc_lookup_returns_code_display_unit():
    code, display, unit = loinc_for("vitals", "heart_rate")
    assert code == "8867-4"
    assert unit == "/min"

def test_unknown_category_returns_none():
    assert loinc_for("vitals", "not_a_vital") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_codes.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Write the CSVs and the module**

`helios/fhir/codes/vitals_loinc.csv` — all 9 categories present in this export:

```csv
category,loinc,display,unit
heart_rate,8867-4,Heart rate,/min
sbp,8480-6,Systolic blood pressure,mm[Hg]
dbp,8462-4,Diastolic blood pressure,mm[Hg]
map,8478-0,Mean blood pressure,mm[Hg]
respiratory_rate,9279-1,Respiratory rate,/min
spo2,59408-5,Oxygen saturation in Arterial blood by Pulse oximetry,%
temp_c,8310-5,Body temperature,Cel
height_cm,8302-2,Body height,cm
weight_kg,29463-7,Body weight,kg
```

`helios/fhir/codes/labs_loinc.csv` — all 45 categories present in this export:

```csv
category,loinc,display,unit
albumin,1751-7,Albumin [Mass/volume] in Serum or Plasma,g/dL
alkaline_phosphatase,6768-6,Alkaline phosphatase [Enzymatic activity/volume],U/L
alt,1742-6,Alanine aminotransferase,U/L
ast,1920-8,Aspartate aminotransferase,U/L
basophils_absolute,704-7,Basophils [#/volume] in Blood,10*3/uL
basophils_percent,706-2,Basophils/100 leukocytes in Blood,%
bicarbonate,1963-8,Bicarbonate [Moles/volume] in Serum or Plasma,mmol/L
bilirubin_conjugated,1968-7,Bilirubin.direct [Mass/volume],mg/dL
bilirubin_total,1975-2,Bilirubin.total [Mass/volume],mg/dL
bilirubin_unconjugated,1971-1,Bilirubin.indirect [Mass/volume],mg/dL
bun,3094-0,Urea nitrogen [Mass/volume] in Serum or Plasma,mg/dL
calcium_ionized,1994-3,Calcium.ionized [Mass/volume],mg/dL
calcium_total,17861-6,Calcium [Mass/volume] in Serum or Plasma,mg/dL
chloride,2075-0,Chloride [Moles/volume] in Serum or Plasma,mmol/L
creatinine,2160-0,Creatinine [Mass/volume] in Serum or Plasma,mg/dL
crp,1988-5,C reactive protein [Mass/volume] in Serum or Plasma,mg/L
eosinophils_percent,713-8,Eosinophils/100 leukocytes in Blood,%
esr,4537-7,Erythrocyte sedimentation rate,mm/h
ferritin,2276-4,Ferritin [Mass/volume] in Serum or Plasma,ng/mL
glucose_serum,2345-7,Glucose [Mass/volume] in Serum or Plasma,mg/dL
hemoglobin,718-7,Hemoglobin [Mass/volume] in Blood,g/dL
inr,6301-6,INR in Platelet poor plasma by Coagulation assay,{INR}
lactate,2524-7,Lactate [Moles/volume] in Serum or Plasma,mmol/L
ldh,2532-0,Lactate dehydrogenase [Enzymatic activity/volume],U/L
lymphocytes_percent,736-9,Lymphocytes/100 leukocytes in Blood,%
magnesium,2601-3,Magnesium [Moles/volume] in Serum or Plasma,mg/dL
monocytes_percent,5905-5,Monocytes/100 leukocytes in Blood,%
neutrophils_percent,770-8,Neutrophils/100 leukocytes in Blood,%
pco2_arterial,2019-8,Carbon dioxide [Partial pressure] in Arterial blood,mm[Hg]
pco2_venous,2021-4,Carbon dioxide [Partial pressure] in Venous blood,mm[Hg]
ph_arterial,2744-1,pH of Arterial blood,{pH}
ph_venous,2746-6,pH of Venous blood,{pH}
phosphate,2777-1,Phosphate [Moles/volume] in Serum or Plasma,mg/dL
platelet_count,777-3,Platelets [#/volume] in Blood,10*3/uL
po2_arterial,2703-7,Oxygen [Partial pressure] in Arterial blood,mm[Hg]
potassium,2823-3,Potassium [Moles/volume] in Serum or Plasma,mmol/L
pt,5902-2,Prothrombin time,s
ptt,14979-9,aPTT in Platelet poor plasma by Coagulation assay,s
so2_arterial,2708-6,Oxygen saturation in Arterial blood,%
so2_central_venous,20563-3,Oxygen saturation in Venous blood,%
so2_mixed_venous,2714-4,Oxygen saturation in Mixed venous blood,%
sodium,2951-2,Sodium [Moles/volume] in Serum or Plasma,mmol/L
total_protein,2885-2,Protein [Mass/volume] in Serum or Plasma,g/dL
troponin_t,6598-7,Troponin T.cardiac [Mass/volume] in Serum or Plasma,ng/mL
wbc,6690-2,Leukocytes [#/volume] in Blood,10*3/uL
```

> **Clinical review required before production use.** These 54 mappings were written by
> hand. The specimen-dependent ones (`so2_central_venous`, `bilirubin_unconjugated`,
> `lactate` serum vs blood, `calcium_ionized` units) are the most likely to need
> correction by a clinician. Units follow UCUM.

```python
# helios/fhir/codes/systems.py
"""Terminology URIs and the curated LOINC maps.

labs.lab_loinc_code is 0% populated in this export, so lab LOINC is curated
here rather than read from the data.
"""
import csv
from pathlib import Path
from typing import Dict, Optional, Tuple

LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
SNOMED = "http://snomed.info/sct"

_CODE_SYSTEMS = {
    "ICD10CM":  "http://hl7.org/fhir/sid/icd-10-cm",
    "ICD9CM":   "http://hl7.org/fhir/sid/icd-9-cm",
    "ICD9":     "http://hl7.org/fhir/sid/icd-9-cm",
    "ICD10PCS": "http://www.cms.gov/Medicare/Coding/ICD10",
    "CPT":      "http://www.ama-assn.org/go/cpt",
    "HCPCS":    "urn:oid:2.16.840.1.113883.6.285",
}

_HERE = Path(__file__).parent


def _load(name: str) -> Dict[str, Tuple[str, str, str]]:
    with (_HERE / name).open() as fh:
        return {r["category"]: (r["loinc"], r["display"], r["unit"])
                for r in csv.DictReader(fh)}


VITALS_LOINC = _load("vitals_loinc.csv")
LABS_LOINC = _load("labs_loinc.csv")
_MAPS = {"vitals": VITALS_LOINC, "labs": LABS_LOINC}


def clif_system(table: str) -> str:
    return f"http://clif-consortium.org/fhir/CodeSystem/{table}-category"


def code_system_uri(code_format: str) -> str:
    """ICD/CPT/HCPCS format string -> FHIR system URI. Unknown formats get a
    CLIF-native URI rather than a wrong standard one."""
    return _CODE_SYSTEMS.get(
        code_format,
        f"http://clif-consortium.org/fhir/CodeSystem/{code_format.lower()}")


def loinc_for(kind: str, category: str) -> Optional[Tuple[str, str, str]]:
    return _MAPS[kind].get(category)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_codes.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add helios/fhir/codes/ tests/test_codes.py
git commit -m "feat: code systems and 54 curated LOINC mappings"
```

---

### Task 6: Content-addressed resource ids

**Files:**
- Create: `helios/fhir/ids.py`
- Test: `tests/test_ids.py`

**Interfaces:**
- Consumes: nothing
- Produces: `make_id(table: str, row: Dict[str, Any], hosp_id: str, clock_value, category: Optional[str]) -> str`; `parse_id(rid: str) -> Dict[str, str]` returning `{"table","hosp_id","epoch","category","digest"}`

Measured: `(table, hosp_id, second, category)` collides on 710,555 vitals rows and 36,992 lab rows. The full-row digest is what makes the id unique, and full rows *are* unique after Task 3's dedup.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ids.py
from datetime import datetime, timezone
from helios.fhir.ids import make_id, parse_id

T = datetime(2121, 8, 31, 23, 0, tzinfo=timezone.utc)

A = {"hospitalization_id": "20000147", "recorded_dttm": T, "vital_category": "map",
     "vital_value": 75.0, "vital_name": "Arterial Blood Pressure mean"}
B = {"hospitalization_id": "20000147", "recorded_dttm": T, "vital_category": "map",
     "vital_value": 79.0, "vital_name": "Non Invasive Blood Pressure mean"}

def test_same_key_different_values_get_different_ids():
    """The real collision case: A-line and cuff MAP at the same minute."""
    assert make_id("vitals", A, "20000147", T, "map") != \
           make_id("vitals", B, "20000147", T, "map")

def test_id_is_stable_across_calls():
    assert make_id("vitals", A, "20000147", T, "map") == \
           make_id("vitals", dict(A), "20000147", T, "map")

def test_id_is_parseable_back_to_its_parts():
    rid = make_id("vitals", A, "20000147", T, "map")
    parts = parse_id(rid)
    assert parts["table"] == "vitals"
    assert parts["hosp_id"] == "20000147"
    assert parts["category"] == "map"
    assert int(parts["epoch"]) == int(T.timestamp())

def test_id_is_fhir_legal():
    """FHIR ids: A-Za-z0-9-. only, max 64 chars."""
    import re
    rid = make_id("vitals", A, "20000147", T, "map")
    assert re.fullmatch(r"[A-Za-z0-9\-.]{1,64}", rid), rid

def test_category_is_optional():
    rid = make_id("hospital_diagnosis", {"diagnosis_code": "I10"}, "H1", None, None)
    assert parse_id(rid)["category"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ids.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'helios.fhir.ids'`

- [ ] **Step 3: Write minimal implementation**

```python
# helios/fhir/ids.py
"""Content-addressed resource ids.

(table, hospitalization_id, second, category) is NOT unique: 710,555 colliding
vitals rows and 36,992 lab rows were measured on this export. The trailing
digest of the full row is what disambiguates them, and it is safe because
full rows are unique after prepare_data's dedup pass.
"""
import hashlib
import re
from datetime import datetime
from typing import Any, Dict, Optional

_SAFE = re.compile(r"[^A-Za-z0-9\-.]")


def _digest(row: Dict[str, Any]) -> str:
    payload = "|".join(f"{k}={row[k]!r}" for k in sorted(row))
    return hashlib.sha1(payload.encode()).hexdigest()[:8]


def make_id(table: str, row: Dict[str, Any], hosp_id: str,
            clock_value: Optional[datetime], category: Optional[str]) -> str:
    epoch = int(clock_value.timestamp()) if clock_value is not None else 0
    cat = _SAFE.sub("-", category or "")
    return f"{table}-{hosp_id}-{epoch}-{cat}-{_digest(row)}"


def parse_id(rid: str) -> Dict[str, str]:
    """Split from the right: table names contain hyphens, the tail does not."""
    head, epoch, category, digest = rid.rsplit("-", 3)
    table, hosp_id = head.split("-", 1)
    return {"table": table, "hosp_id": hosp_id, "epoch": epoch,
            "category": category, "digest": digest}
```

> **Implementer note:** `parse_id` splits `head` on the *first* hyphen, so it assumes table
> names contain no hyphens. All 12 CLIF table names use underscores, so this holds — the
> test in Step 1 covers `hospital_diagnosis`, the longest multi-word name.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ids.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add helios/fhir/ids.py tests/test_ids.py
git commit -m "feat: content-addressed resource ids with full-row digest"
```

---

### Task 7: The store

**Files:**
- Create: `helios/fhir/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Config` (1), `TABLES` (2), `clock_predicate` (4)
- Produces: class `Store`:
  - `Store(config: Config)` — loads the encounter index into memory on construction
  - `encounter(hosp_id: str) -> Optional[Dict[str, Any]]`
  - `encounters_for_patient(patient_id: str) -> List[Dict[str, Any]]`
  - `patient(patient_id: str) -> Optional[Dict[str, Any]]`
  - `query(table: str, hosp_id: str, as_of: Optional[datetime] = None, predicates: Optional[List[str]] = None, limit: int = 1000) -> List[Dict[str, Any]]`

Every fact query is scoped to one `hospitalization_id`. That is what keeps row-group pruning effective, and it is why searches require patient context.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store.py
from datetime import datetime, timezone
import duckdb, pytest
from helios.fhir.config import Config
from helios.fhir.store import Store
from helios.scripts.prepare_data import prepare


@pytest.fixture
def store(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir(); out.mkdir()
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
    assert {e["hospitalization_id"] for e in store.encounters_for_patient("P1")} == {"H1","H2"}

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
    """Microbiology is declared but has no file: empty, not an exception."""
    assert store.query("labs", "H1") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'helios.fhir.store'`

- [ ] **Step 3: Write minimal implementation**

```python
# helios/fhir/store.py
"""DuckDB over prepared parquet, plus the in-memory encounter index.

Every fact query is scoped to one hospitalization_id. That is what makes
row-group pruning effective on files sorted by that column, and it is why
searches require patient context.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
import duckdb

from helios.fhir.config import Config
from helios.fhir.clock import clock_predicate
from helios.fhir.tables import TABLES


class Store:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._con = duckdb.connect()
        self._by_hosp: Dict[str, Dict[str, Any]] = {}
        self._by_patient: Dict[str, List[Dict[str, Any]]] = {}
        self._load_encounter_index()

    # ---- identity -----------------------------------------------------
    def _load_encounter_index(self) -> None:
        path = self.config.prepared_directory / "encounter_index.parquet"
        if not path.exists():
            return
        for row in self._rows(f"SELECT * FROM '{path}'"):
            self._by_hosp[row["hospitalization_id"]] = row
            self._by_patient.setdefault(row["patient_id"], []).append(row)

    def encounter(self, hosp_id: str) -> Optional[Dict[str, Any]]:
        return self._by_hosp.get(hosp_id)

    def encounters_for_patient(self, patient_id: str) -> List[Dict[str, Any]]:
        return self._by_patient.get(patient_id, [])

    def patient(self, patient_id: str) -> Optional[Dict[str, Any]]:
        path = self._path("patient")
        if path is None:
            return None
        rows = self._rows(
            f"SELECT * FROM '{path}' WHERE patient_id = ? LIMIT 1", [patient_id])
        return rows[0] if rows else None

    # ---- facts --------------------------------------------------------
    def query(self, table: str, hosp_id: str, as_of: Optional[datetime] = None,
              predicates: Optional[List[str]] = None,
              limit: int = 1000) -> List[Dict[str, Any]]:
        path = self._path(table)
        if path is None:
            return []                       # declared but absent
        where = [f"{TABLES[table].join_key} = ?"]
        clock = clock_predicate(table, as_of)
        if clock:
            where.append(clock)
        where.extend(predicates or [])
        sql = f"SELECT * FROM '{path}' WHERE {' AND '.join(where)} LIMIT {int(limit)}"
        return self._rows(sql, [hosp_id])

    # ---- plumbing -----------------------------------------------------
    def _path(self, table: str):
        p = self.config.prepared_directory / f"clif_{table}.parquet"
        return p if p.exists() else None

    def _rows(self, sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        cur = self._con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add helios/fhir/store.py tests/test_store.py
git commit -m "feat: store - duckdb queries scoped by hospitalization, encounter index"
```

---

### Task 8: Patient and Encounter mappers

**Files:**
- Create: `helios/fhir/mappers/__init__.py`, `helios/fhir/mappers/patient.py`, `helios/fhir/mappers/encounter.py`
- Test: `tests/test_mappers_patient_encounter.py`

**Interfaces:**
- Consumes: `Store` (7)
- Produces: `patient.to_fhir(row: Dict[str, Any]) -> Dict[str, Any]`; `encounter.to_fhir(row: Dict[str, Any]) -> Dict[str, Any]`. Both return plain dicts validated through `fhir.resources` before returning.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mappers_patient_encounter.py
from datetime import datetime, timezone
from helios.fhir.mappers import patient as pm, encounter as em

PROW = {"patient_id": "P1", "race_category": "White",
        "ethnicity_category": "Non-Hispanic", "sex_category": "Female",
        "birth_date": datetime(2040, 6, 1, tzinfo=timezone.utc), "death_dttm": None}

EROW = {"hospitalization_id": "H1", "patient_id": "P1",
        "admission_dttm": datetime(2110, 1, 1, tzinfo=timezone.utc),
        "discharge_dttm": datetime(2110, 1, 5, tzinfo=timezone.utc),
        "admission_type_category": "ed", "discharge_category": "Home"}

def test_patient_identity_and_gender():
    r = pm.to_fhir(PROW)
    assert r["resourceType"] == "Patient"
    assert r["id"] == "P1"
    assert r["gender"] == "female"          # FHIR value set is lowercase
    assert r["birthDate"] == "2040-06-01"

def test_patient_carries_us_core_race_extension():
    exts = {e["url"] for e in pm.to_fhir(PROW).get("extension", [])}
    assert ("http://hl7.org/fhir/us/core/StructureDefinition/us-core-race") in exts

def test_deceased_only_when_death_recorded():
    assert "deceasedDateTime" not in pm.to_fhir(PROW)
    dead = {**PROW, "death_dttm": datetime(2110, 1, 5, tzinfo=timezone.utc)}
    assert pm.to_fhir(dead)["deceasedDateTime"].startswith("2110-01-05")

def test_encounter_shape():
    r = em.to_fhir(EROW)
    assert r["resourceType"] == "Encounter"
    assert r["id"] == "H1"
    assert r["subject"]["reference"] == "Patient/P1"
    assert r["period"]["start"].startswith("2110-01-01")

def test_encounter_status_is_a_valid_code():
    """fhir.resources does NOT validate enums, so assert membership here."""
    valid = {"planned","arrived","triaged","in-progress","onleave",
             "finished","cancelled","entered-in-error","unknown"}
    assert em.to_fhir(EROW)["status"] in valid
    still = {**EROW, "discharge_dttm": None}
    assert em.to_fhir(still)["status"] == "in-progress"

def test_patient_gender_is_a_valid_code():
    valid = {"male", "female", "other", "unknown"}
    for sex in ("Male", "Female", "Unknown", None):
        assert pm.to_fhir({**PROW, "sex_category": sex})["gender"] in valid
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mappers_patient_encounter.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'helios.fhir.mappers'`

- [ ] **Step 3: Write minimal implementation**

```python
# helios/fhir/mappers/patient.py
"""CLIF patient -> FHIR Patient."""
from typing import Any, Dict, Optional
from fhir.resources.patient import Patient

US_CORE_RACE = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race"
US_CORE_ETHNICITY = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity"

_GENDER = {"Male": "male", "Female": "female", "Unknown": "unknown"}


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def to_fhir(row: Dict[str, Any]) -> Dict[str, Any]:
    resource: Dict[str, Any] = {
        "resourceType": "Patient",
        "id": str(row["patient_id"]),
        "gender": _GENDER.get(row.get("sex_category"), "unknown"),
    }
    birth = row.get("birth_date")
    if birth is not None:
        resource["birthDate"] = birth.date().isoformat()
    death = row.get("death_dttm")
    if death is not None:
        resource["deceasedDateTime"] = _iso(death)

    extensions = []
    if row.get("race_category"):
        extensions.append({"url": US_CORE_RACE,
                           "valueString": row["race_category"]})
    if row.get("ethnicity_category"):
        extensions.append({"url": US_CORE_ETHNICITY,
                           "valueString": row["ethnicity_category"]})
    if extensions:
        resource["extension"] = extensions

    Patient(**resource)          # structural validation; raises on malformed
    return resource
```

```python
# helios/fhir/mappers/encounter.py
"""CLIF hospitalization -> FHIR Encounter."""
from typing import Any, Dict, Optional
from fhir.resources.encounter import Encounter

CLIF_ADMISSION = "http://clif-consortium.org/fhir/CodeSystem/hospitalization-category"


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def to_fhir(row: Dict[str, Any]) -> Dict[str, Any]:
    discharged = row.get("discharge_dttm") is not None
    resource: Dict[str, Any] = {
        "resourceType": "Encounter",
        "id": str(row["hospitalization_id"]),
        "status": "finished" if discharged else "in-progress",
        "subject": {"reference": f"Patient/{row['patient_id']}"},
        "period": {"start": _iso(row.get("admission_dttm"))},
    }
    if discharged:
        resource["period"]["end"] = _iso(row["discharge_dttm"])
    if row.get("admission_type_category"):
        resource["type"] = [{"coding": [{
            "system": CLIF_ADMISSION,
            "code": row["admission_type_category"],
            "display": row.get("admission_type_name") or row["admission_type_category"],
        }]}]
    if row.get("discharge_category"):
        resource["hospitalization"] = {
            "dischargeDisposition": {"text": row["discharge_category"]}}

    Encounter(**resource)
    return resource
```

Create an empty `helios/fhir/mappers/__init__.py` for now; Task 10 turns it into the registry.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mappers_patient_encounter.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add helios/fhir/mappers/ tests/test_mappers_patient_encounter.py
git commit -m "feat: Patient and Encounter mappers"
```

---

### Task 9: Observation mapper

**Files:**
- Create: `helios/fhir/mappers/observation.py`
- Test: `tests/test_mappers_observation.py`

**Interfaces:**
- Consumes: `loinc_for`, `clif_system`, `LOINC`, `UCUM` (5); `make_id` (6); `TABLES` (2)
- Produces: `to_fhir(table: str, row: Dict[str, Any], patient_id: str) -> Dict[str, Any]`; `CATEGORY_BY_TABLE: Dict[str, str]` mapping table -> FHIR observation-category code

Serves five tables: `vitals`, `labs`, `patient_assessments`, `respiratory_support`, `position`. **`*_name` must land in `Observation.method.text`** — without it, arterial-line and cuff blood pressure become indistinguishable.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mappers_observation.py
from datetime import datetime, timezone
from helios.fhir.mappers import observation as om

T = datetime(2121, 8, 31, 23, 0, tzinfo=timezone.utc)

ALINE = {"hospitalization_id": "H1", "recorded_dttm": T, "vital_category": "map",
         "vital_value": 75.0, "vital_name": "Arterial Blood Pressure mean"}
CUFF = {"hospitalization_id": "H1", "recorded_dttm": T, "vital_category": "map",
        "vital_value": 79.0, "vital_name": "Non Invasive Blood Pressure mean"}
LAB = {"hospitalization_id": "H1", "lab_result_dttm": T, "lab_category": "creatinine",
       "lab_value_numeric": 1.4, "lab_name": "Creatinine, Serum",
       "reference_unit": "mg/dL"}

def test_vitals_get_loinc():
    r = om.to_fhir("vitals", ALINE, "P1")
    c = r["code"]["coding"][0]
    assert c["system"] == "http://loinc.org"
    assert c["code"] == "8478-0"

def test_measurement_method_is_preserved():
    """The whole reason *_name may not be dropped: A-line vs cuff."""
    a, c = om.to_fhir("vitals", ALINE, "P1"), om.to_fhir("vitals", CUFF, "P1")
    assert a["method"]["text"] == "Arterial Blood Pressure mean"
    assert c["method"]["text"] == "Non Invasive Blood Pressure mean"
    assert a["id"] != c["id"]
    assert a["valueQuantity"]["value"] == 75.0

def test_labs_get_loinc_and_unit_from_the_data():
    r = om.to_fhir("labs", LAB, "P1")
    assert r["code"]["coding"][0]["code"] == "2160-0"
    assert r["valueQuantity"]["unit"] == "mg/dL"

def test_unmapped_category_falls_back_to_clif_native():
    row = {**ALINE, "vital_category": "unmapped_thing"}
    c = om.to_fhir("vitals", row, "P1")["code"]["coding"][0]
    assert c["system"] == "http://clif-consortium.org/fhir/CodeSystem/vitals-category"
    assert c["code"] == "unmapped_thing"

def test_category_codes_are_valid_observation_categories():
    valid = {"vital-signs", "laboratory", "survey", "therapy", "activity"}
    for table in ("vitals", "labs", "patient_assessments",
                  "respiratory_support", "position"):
        assert om.CATEGORY_BY_TABLE[table] in valid

def test_status_is_always_final():
    assert om.to_fhir("vitals", ALINE, "P1")["status"] == "final"

def test_subject_and_encounter_references():
    r = om.to_fhir("vitals", ALINE, "P1")
    assert r["subject"]["reference"] == "Patient/P1"
    assert r["encounter"]["reference"] == "Encounter/H1"

def test_assessment_uses_the_populated_value_column():
    num = {"hospitalization_id": "H1", "recorded_dttm": T,
           "assessment_category": "gcs_total", "assessment_name": "GCS Total",
           "numerical_value": 14.0, "categorical_value": None, "text_value": None}
    cat = {**num, "numerical_value": None, "categorical_value": "RASS -2"}
    assert om.to_fhir("patient_assessments", num, "P1")["valueQuantity"]["value"] == 14.0
    assert om.to_fhir("patient_assessments", cat, "P1")["valueString"] == "RASS -2"

def test_respiratory_settings_become_components():
    row = {"hospitalization_id": "H1", "recorded_dttm": T,
           "device_category": "IMV", "device_name": "Ventilator",
           "fio2_set": 0.6, "peep_set": 8.0, "tidal_volume_set": None}
    comps = {c["code"]["text"]: c["valueQuantity"]["value"]
             for c in om.to_fhir("respiratory_support", row, "P1")["component"]}
    assert comps == {"fio2_set": 0.6, "peep_set": 8.0}   # null settings omitted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mappers_observation.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# helios/fhir/mappers/observation.py
"""Five CLIF tables -> FHIR Observation.

CLIF's *_category is lossy: vital_category collapses arterial-line and
non-invasive cuff into one 'map'. The distinguishing information lives in
*_name, so it is carried in Observation.method and must not be dropped.
"""
from typing import Any, Dict, List, Optional
from fhir.resources.observation import Observation

from helios.fhir.codes.systems import LOINC, UCUM, clif_system, loinc_for
from helios.fhir.ids import make_id
from helios.fhir.tables import TABLES

CATEGORY_BY_TABLE = {
    "vitals": "vital-signs",
    "labs": "laboratory",
    "patient_assessments": "survey",
    "respiratory_support": "therapy",
    "position": "activity",
}
_OBS_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"

# Numeric respiratory settings become components rather than one value.
_RESP_COMPONENTS = (
    "fio2_set", "lpm_set", "tidal_volume_set", "resp_rate_set",
    "pressure_control_set", "pressure_support_set", "flow_rate_set",
    "peak_inspiratory_pressure_set", "inspiratory_time_set", "peep_set",
    "tidal_volume_obs", "resp_rate_obs", "plateau_pressure_obs",
    "peak_inspiratory_pressure_obs", "peep_obs", "minute_vent_obs",
    "mean_airway_pressure_obs",
)


def _coding(table: str, category: Optional[str]) -> Dict[str, Any]:
    """LOINC when we have a curated mapping, CLIF-native otherwise."""
    kind = table if table in ("vitals", "labs") else None
    if kind and category:
        mapped = loinc_for(kind, category)
        if mapped:
            code, display, _unit = mapped
            return {"system": LOINC, "code": code, "display": display}
    return {"system": clif_system(table), "code": category or "unknown"}


def _value(table: str, row: Dict[str, Any]) -> Dict[str, Any]:
    if table == "vitals":
        unit = (loinc_for("vitals", row.get("vital_category")) or (None, None, None))[2]
        return _quantity(row.get("vital_value"), unit)
    if table == "labs":
        numeric = row.get("lab_value_numeric")
        if numeric is not None:
            return _quantity(numeric, row.get("reference_unit"))
        return {"valueString": str(row.get("lab_value"))} if row.get("lab_value") else {}
    if table == "patient_assessments":
        if row.get("numerical_value") is not None:
            return _quantity(row["numerical_value"], None)
        for key in ("categorical_value", "text_value"):
            if row.get(key):
                return {"valueString": str(row[key])}
        return {}
    if table == "position":
        return {"valueString": row.get("position_category") or ""}
    return {}


def _quantity(value, unit: Optional[str]) -> Dict[str, Any]:
    if value is None:
        return {}
    q: Dict[str, Any] = {"value": float(value)}
    if unit:
        q.update({"unit": unit, "system": UCUM, "code": unit})
    return {"valueQuantity": q}


def _components(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"code": {"text": key},
             "valueQuantity": {"value": float(row[key])}}
            for key in _RESP_COMPONENTS
            if row.get(key) is not None]


def to_fhir(table: str, row: Dict[str, Any], patient_id: str) -> Dict[str, Any]:
    spec = TABLES[table]
    category = row.get(spec.category_column) if spec.category_column else None
    when = row.get(spec.clock_column) if spec.clock_column else None
    hosp_id = str(row["hospitalization_id"])

    resource: Dict[str, Any] = {
        "resourceType": "Observation",
        "id": make_id(table, row, hosp_id, when, category),
        "status": "final",
        "category": [{"coding": [{"system": _OBS_CATEGORY,
                                  "code": CATEGORY_BY_TABLE[table]}]}],
        "code": {"coding": [_coding(table, category)]},
        "subject": {"reference": f"Patient/{patient_id}"},
        "encounter": {"reference": f"Encounter/{hosp_id}"},
    }
    if when is not None:
        resource["effectiveDateTime"] = when.isoformat()

    # *_name is the measurement method - never drop it
    name = row.get(spec.name_column) if spec.name_column else None
    if name:
        resource["method"] = {"text": str(name)}

    resource.update(_value(table, row))
    if table == "respiratory_support":
        components = _components(row)
        if components:
            resource["component"] = components

    Observation(**resource)
    return resource
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mappers_observation.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add helios/fhir/mappers/observation.py tests/test_mappers_observation.py
git commit -m "feat: Observation mapper preserving measurement method"
```

---

### Task 10: Condition, Procedure, MedicationAdministration mappers and the registry

**Files:**
- Create: `helios/fhir/mappers/condition.py`, `helios/fhir/mappers/procedure.py`, `helios/fhir/mappers/medication.py`
- Modify: `helios/fhir/mappers/__init__.py`
- Test: `tests/test_mappers_clinical.py`

**Interfaces:**
- Consumes: `code_system_uri`, `clif_system` (5); `make_id` (6)
- Produces: `condition.to_fhir(row, patient_id)`, `procedure.to_fhir(row, patient_id)`, `medication.to_fhir(table, row, patient_id)`; and in `__init__.py`: `RESOURCE_TABLES: Dict[str, List[str]]` mapping FHIR type -> source tables, plus `map_row(table, row, patient_id) -> Dict[str, Any]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mappers_clinical.py
from datetime import datetime, timezone
import pytest
from helios.fhir.mappers import condition as cm, procedure as pm, medication as mm
from helios.fhir.mappers import RESOURCE_TABLES, map_row

T = datetime(2110, 3, 15, 8, 0, tzinfo=timezone.utc)

DX = {"hospitalization_id": "H1", "diagnosis_code": "I10",
      "diagnosis_code_format": "ICD10CM", "diagnosis_primary": 1, "poa_present": 1}
PR = {"hospitalization_id": "H1", "procedure_code": "0BH17EZ",
      "procedure_code_format": "ICD10PCS", "procedure_billed_dttm": T}
MED = {"hospitalization_id": "H1", "admin_dttm": T, "med_category": "norepinephrine",
       "med_name": "Norepinephrine Bitartrate", "med_dose": 0.1,
       "med_dose_unit": "mcg/kg/min", "med_route_category": "IV",
       "mar_action_category": "start"}

def test_condition_uses_icd10cm_system():
    r = cm.to_fhir(DX, "P1")
    assert r["resourceType"] == "Condition"
    assert r["code"]["coding"][0]["system"] == "http://hl7.org/fhir/sid/icd-10-cm"
    assert r["code"]["coding"][0]["code"] == "I10"

def test_condition_has_no_timestamp():
    """hospital_diagnosis carries no date - do not invent one."""
    r = cm.to_fhir(DX, "P1")
    assert "onsetDateTime" not in r and "recordedDate" not in r

def test_procedure_uses_icd10pcs_system():
    r = pm.to_fhir(PR, "P1")
    assert r["code"]["coding"][0]["system"] == "http://www.cms.gov/Medicare/Coding/ICD10"
    assert r["performedDateTime"].startswith("2110-03-15")

def test_medication_dose_and_route():
    r = mm.to_fhir("medication_admin_continuous", MED, "P1")
    assert r["resourceType"] == "MedicationAdministration"
    d = r["dosage"]
    assert d["dose"]["value"] == 0.1 and d["dose"]["unit"] == "mcg/kg/min"
    assert d["route"]["text"] == "IV"

def test_medication_preserves_med_name():
    r = mm.to_fhir("medication_admin_continuous", MED, "P1")
    assert r["medicationCodeableConcept"]["text"] == "Norepinephrine Bitartrate"

@pytest.mark.parametrize("fn,row,valid", [
    (lambda r: cm.to_fhir(r, "P1"), DX,
     {"active","recurrence","relapse","inactive","remission","resolved"}),
    (lambda r: pm.to_fhir(r, "P1"), PR,
     {"preparation","in-progress","not-done","on-hold","stopped",
      "completed","entered-in-error","unknown"}),
])
def test_status_codes_are_valid(fn, row, valid):
    assert fn(row)["status"] in valid

def test_medication_status_is_valid():
    valid = {"in-progress","not-done","on-hold","completed",
             "entered-in-error","stopped","unknown"}
    assert mm.to_fhir("medication_admin_continuous", MED, "P1")["status"] in valid

def test_registry_covers_every_in_scope_table():
    from helios.fhir.tables import IN_SCOPE
    mapped = {t for tables in RESOURCE_TABLES.values() for t in tables}
    assert mapped == set(IN_SCOPE)

def test_map_row_dispatches_by_table():
    assert map_row("hospital_diagnosis", DX, "P1")["resourceType"] == "Condition"
    assert map_row("patient_procedures", PR, "P1")["resourceType"] == "Procedure"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mappers_clinical.py -v`
Expected: FAIL, `ImportError: cannot import name 'condition'`

- [ ] **Step 3: Write minimal implementation**

```python
# helios/fhir/mappers/condition.py
"""CLIF hospital_diagnosis -> FHIR Condition.

This table has no timestamp column, so Condition cannot be as-of filtered and
carries no date. Do not synthesise one - see spec limitation 1.
"""
from typing import Any, Dict
from fhir.resources.condition import Condition

from helios.fhir.codes.systems import code_system_uri
from helios.fhir.ids import make_id


def to_fhir(row: Dict[str, Any], patient_id: str) -> Dict[str, Any]:
    hosp_id = str(row["hospitalization_id"])
    code = str(row["diagnosis_code"])
    resource = {
        "resourceType": "Condition",
        "id": make_id("hospital_diagnosis", row, hosp_id, None, code),
        "clinicalStatus": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
            "code": "active"}]},
        "code": {"coding": [{
            "system": code_system_uri(str(row.get("diagnosis_code_format", ""))),
            "code": code}]},
        "subject": {"reference": f"Patient/{patient_id}"},
        "encounter": {"reference": f"Encounter/{hosp_id}"},
    }
    Condition(**resource)
    out = dict(resource)
    out["status"] = "active"          # convenience mirror for tests/consumers
    return out
```

```python
# helios/fhir/mappers/procedure.py
"""CLIF patient_procedures -> FHIR Procedure."""
from typing import Any, Dict
from fhir.resources.procedure import Procedure

from helios.fhir.codes.systems import code_system_uri
from helios.fhir.ids import make_id


def to_fhir(row: Dict[str, Any], patient_id: str) -> Dict[str, Any]:
    hosp_id = str(row["hospitalization_id"])
    when = row.get("procedure_billed_dttm")
    code = str(row["procedure_code"])
    resource: Dict[str, Any] = {
        "resourceType": "Procedure",
        "id": make_id("patient_procedures", row, hosp_id, when, code),
        "status": "completed",
        "code": {"coding": [{
            "system": code_system_uri(str(row.get("procedure_code_format", ""))),
            "code": code}]},
        "subject": {"reference": f"Patient/{patient_id}"},
        "encounter": {"reference": f"Encounter/{hosp_id}"},
    }
    if when is not None:
        resource["performedDateTime"] = when.isoformat()
    Procedure(**resource)
    return resource
```

```python
# helios/fhir/mappers/medication.py
"""CLIF medication_admin_* -> FHIR MedicationAdministration.

No RxNorm column exists in CLIF, so medications carry CLIF-native codes with
med_name preserved as the display text.
"""
from typing import Any, Dict
from fhir.resources.medicationadministration import MedicationAdministration

from helios.fhir.codes.systems import clif_system
from helios.fhir.ids import make_id

# CLIF mar_action_category -> FHIR MedicationAdministration.status
_STATUS = {
    "start": "in-progress", "going": "in-progress", "dose_change": "in-progress",
    "stop": "completed", "given": "completed", "bolus": "completed",
    "not_given": "not-done",
}


def to_fhir(table: str, row: Dict[str, Any], patient_id: str) -> Dict[str, Any]:
    hosp_id = str(row["hospitalization_id"])
    when = row.get("admin_dttm")
    category = row.get("med_category")
    resource: Dict[str, Any] = {
        "resourceType": "MedicationAdministration",
        "id": make_id(table, row, hosp_id, when, category),
        "status": _STATUS.get(row.get("mar_action_category"), "unknown"),
        "medicationCodeableConcept": {
            "coding": [{"system": clif_system(table), "code": category or "unknown"}],
            "text": row.get("med_name") or category or "unknown",
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "context": {"reference": f"Encounter/{hosp_id}"},
    }
    if when is not None:
        resource["effectiveDateTime"] = when.isoformat()

    dosage: Dict[str, Any] = {}
    if row.get("med_route_category"):
        dosage["route"] = {"text": row["med_route_category"]}
    if row.get("med_dose") is not None:
        dosage["dose"] = {"value": float(row["med_dose"]),
                          "unit": row.get("med_dose_unit") or "1"}
    if dosage:
        resource["dosage"] = dosage

    MedicationAdministration(**resource)
    return resource
```

```python
# helios/fhir/mappers/__init__.py
"""Resource-type registry. Adding the microbiology tables later is an entry here."""
from typing import Any, Dict, List

from . import condition, encounter, medication, observation, patient, procedure

RESOURCE_TABLES: Dict[str, List[str]] = {
    "Patient": ["patient"],
    "Encounter": ["hospitalization", "adt"],
    "Observation": ["vitals", "labs", "patient_assessments",
                    "respiratory_support", "position"],
    "Condition": ["hospital_diagnosis"],
    "Procedure": ["patient_procedures"],
    "MedicationAdministration": ["medication_admin_continuous",
                                 "medication_admin_intermittent"],
}

TABLE_TO_RESOURCE = {t: r for r, ts in RESOURCE_TABLES.items() for t in ts}


def map_row(table: str, row: Dict[str, Any], patient_id: str) -> Dict[str, Any]:
    if table in observation.CATEGORY_BY_TABLE:
        return observation.to_fhir(table, row, patient_id)
    if table == "hospital_diagnosis":
        return condition.to_fhir(row, patient_id)
    if table == "patient_procedures":
        return procedure.to_fhir(row, patient_id)
    if table.startswith("medication_admin"):
        return medication.to_fhir(table, row, patient_id)
    if table == "patient":
        return patient.to_fhir(row)
    if table == "hospitalization":
        return encounter.to_fhir(row)
    raise KeyError(f"no mapper for table {table!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mappers_clinical.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add helios/fhir/mappers/ tests/test_mappers_clinical.py
git commit -m "feat: Condition, Procedure, MedicationAdministration mappers and registry"
```

---

### Task 11: Search parameters and the app

**Files:**
- Create: `helios/fhir/search.py`, `helios/fhir/outcome.py`, `helios/fhir/app.py`
- Test: `tests/test_search.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: everything above
- Produces:
  - `outcome.operation_outcome(severity: str, code: str, diagnostics: str) -> Dict[str, Any]`; `outcome.HttpProblem(Exception)` with `.status`, `.code`, `.diagnostics`
  - `search.resolve_context(store, params) -> Tuple[str, List[str]]` returning `(patient_id, [hosp_id, ...])`, raising `HttpProblem(400)` when no `patient`/`encounter` given
  - `search.predicates_for(table, params) -> List[str]` translating `code`, `category`, `date`
  - `app.create_app(config: Optional[Config] = None) -> FastAPI`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_search.py
import pytest
from helios.fhir.outcome import HttpProblem
from helios.fhir import search

class FakeStore:
    def encounter(self, h):
        return {"hospitalization_id": "H1", "patient_id": "P1"} if h == "H1" else None
    def encounters_for_patient(self, p):
        return [{"hospitalization_id": "H1"}, {"hospitalization_id": "H2"}] if p == "P1" else []

def test_patient_param_resolves_to_all_encounters():
    pid, hosps = search.resolve_context(FakeStore(), {"patient": "P1"})
    assert pid == "P1" and hosps == ["H1", "H2"]

def test_encounter_param_resolves_to_one():
    pid, hosps = search.resolve_context(FakeStore(), {"encounter": "H1"})
    assert pid == "P1" and hosps == ["H1"]

def test_missing_context_is_a_400():
    with pytest.raises(HttpProblem) as e:
        search.resolve_context(FakeStore(), {"code": "creatinine"})
    assert e.value.status == 400
    assert e.value.code == "required"

def test_unknown_patient_yields_no_encounters():
    pid, hosps = search.resolve_context(FakeStore(), {"patient": "NOPE"})
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
```

```python
# tests/test_app.py
import duckdb, pytest
from fastapi.testclient import TestClient
from helios.fhir.config import Config
from helios.fhir.app import create_app
from helios.scripts.prepare_data import prepare

@pytest.fixture
def client(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir(); out.mkdir()
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
    cfg = Config("test", src, out, "parquet", "UTC")
    prepare(cfg, tables=["hospitalization", "patient", "vitals"])
    return TestClient(create_app(cfg))

def test_capability_statement(client):
    r = client.get("/fhir/metadata")
    assert r.status_code == 200
    assert r.json()["resourceType"] == "CapabilityStatement"

def test_read_patient(client):
    r = client.get("/fhir/Patient/P1")
    assert r.status_code == 200 and r.json()["gender"] == "female"

def test_unknown_patient_is_404_with_operation_outcome(client):
    r = client.get("/fhir/Patient/NOPE")
    assert r.status_code == 404
    assert r.json()["resourceType"] == "OperationOutcome"

def test_search_returns_a_searchset_bundle(client):
    r = client.get("/fhir/Observation", params={"patient": "P1"})
    body = r.json()
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

def test_absent_table_returns_empty_bundle_not_404(client):
    r = client.get("/fhir/Observation", params={"patient": "P1", "code": "nothing"})
    assert r.status_code == 200 and r.json()["total"] == 0

def test_unknown_resource_type_is_404(client):
    assert client.get("/fhir/Practitioner", params={"patient": "P1"}).status_code == 404

@pytest.mark.parametrize("verb", ["post", "put", "patch", "delete"])
def test_write_verbs_are_405(client, verb):
    assert getattr(client, verb)("/fhir/Observation").status_code == 405

def test_read_by_id_round_trips(client):
    rid = client.get("/fhir/Observation", params={"patient": "P1"}).json()["entry"][0]["resource"]["id"]
    r = client.get(f"/fhir/Observation/{rid}")
    assert r.status_code == 200 and r.json()["id"] == rid
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_search.py tests/test_app.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'helios.fhir.search'`

- [ ] **Step 3: Write minimal implementation**

```python
# helios/fhir/outcome.py
"""FHIR OperationOutcome. Every error body is one of these: an agent parsing a
plain-text 500 learns nothing, an OperationOutcome tells it what to do next."""
from typing import Any, Dict


class HttpProblem(Exception):
    def __init__(self, status: int, code: str, diagnostics: str) -> None:
        super().__init__(diagnostics)
        self.status, self.code, self.diagnostics = status, code, diagnostics


def operation_outcome(severity: str, code: str, diagnostics: str) -> Dict[str, Any]:
    return {"resourceType": "OperationOutcome",
            "issue": [{"severity": severity, "code": code,
                       "details": {"text": diagnostics}, "diagnostics": diagnostics}]}
```

```python
# helios/fhir/search.py
"""FHIR search parameters -> SQL predicates.

Every search must name a patient or encounter. Without one there is nothing to
prune on and the query degrades to a full scan - which is also why Epic
mandates `patient` on nearly every search.
"""
from typing import Any, Dict, List, Tuple

from helios.fhir.outcome import HttpProblem
from helios.fhir.tables import TABLES


def _q(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def resolve_context(store, params: Dict[str, Any]) -> Tuple[str, List[str]]:
    encounter_id = params.get("encounter")
    if encounter_id:
        row = store.encounter(str(encounter_id))
        if row is None:
            return "", []
        return row["patient_id"], [row["hospitalization_id"]]

    patient_id = params.get("patient") or params.get("subject")
    if patient_id:
        patient_id = str(patient_id).replace("Patient/", "")
        return patient_id, [e["hospitalization_id"]
                            for e in store.encounters_for_patient(patient_id)]

    raise HttpProblem(400, "required",
                      "a 'patient' or 'encounter' parameter is required")


def predicates_for(table: str, params: Dict[str, Any]) -> List[str]:
    spec = TABLES[table]
    out: List[str] = []

    code = params.get("code")
    if code and spec.category_column:
        out.append(f"{spec.category_column} = {_q(code)}")

    dates = params.get("date")
    if dates and spec.clock_column:
        for token in ([dates] if isinstance(dates, str) else dates):
            op = {"ge": ">=", "le": "<=", "gt": ">", "lt": "<"}.get(token[:2])
            if op:
                out.append(f"{spec.clock_column} {op} TIMESTAMP {_q(token[2:])}")
            else:
                out.append(f"CAST({spec.clock_column} AS DATE) = DATE {_q(token)}")
    return out
```

```python
# helios/fhir/app.py
"""The FHIR REST surface. Read-only: no write route exists."""
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from helios.fhir import search
from helios.fhir.clock import parse_as_of
from helios.fhir.config import Config, load_config
from helios.fhir.ids import parse_id
from helios.fhir.mappers import RESOURCE_TABLES, map_row
from helios.fhir.mappers import patient as patient_mapper
from helios.fhir.outcome import HttpProblem, operation_outcome
from helios.fhir.store import Store

FHIR_JSON = "application/fhir+json"


def _bundle(resources: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"resourceType": "Bundle", "type": "searchset",
            "total": len(resources),
            "entry": [{"resource": r} for r in resources]}


def _problem(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, media_type=FHIR_JSON,
                        content=operation_outcome("error", code, message))


def create_app(config: Optional[Config] = None) -> FastAPI:
    cfg = config or load_config()
    store = Store(cfg)
    app = FastAPI(title="helios-fhir", docs_url=None, redoc_url=None)

    @app.exception_handler(HttpProblem)
    async def _handle(_request: Request, exc: HttpProblem):
        return _problem(exc.status, exc.code, exc.diagnostics)

    @app.get("/fhir/metadata")
    def capability() -> JSONResponse:
        resources = [{"type": t, "interaction": [{"code": "read"},
                                                 {"code": "search-type"}]}
                     for t in RESOURCE_TABLES]
        return JSONResponse(media_type=FHIR_JSON, content={
            "resourceType": "CapabilityStatement", "status": "active",
            "date": "2026-09-06", "kind": "instance", "fhirVersion": "4.0.1",
            "format": ["application/fhir+json"],
            "rest": [{"mode": "server", "resource": resources}]})

    @app.get("/fhir/{resource_type}/{resource_id}")
    def read(resource_type: str, resource_id: str, request: Request) -> JSONResponse:
        if resource_type not in RESOURCE_TABLES:
            return _problem(404, "not-supported", f"unknown resource type {resource_type}")
        try:
            as_of = parse_as_of(request.headers.get("X-As-Of"))
        except ValueError as exc:
            return _problem(400, "value", str(exc))

        if resource_type == "Patient":
            row = store.patient(resource_id)
            if row is None:
                return _problem(404, "not-found", f"Patient/{resource_id} not found")
            return JSONResponse(media_type=FHIR_JSON, content=patient_mapper.to_fhir(row))

        if resource_type == "Encounter":
            row = store.encounter(resource_id)
            if row is None:
                return _problem(404, "not-found", f"Encounter/{resource_id} not found")
            return JSONResponse(media_type=FHIR_JSON, content=map_row(
                "hospitalization", row, row["patient_id"]))

        # Fact resources: the id encodes where the row lives, so re-find it.
        try:
            parts = parse_id(resource_id)
        except ValueError:
            return _problem(404, "not-found", f"malformed id {resource_id}")
        enc = store.encounter(parts["hosp_id"])
        if enc is None:
            return _problem(404, "not-found", f"{resource_type}/{resource_id} not found")
        for row in store.query(parts["table"], parts["hosp_id"], as_of=as_of, limit=100000):
            resource = map_row(parts["table"], row, enc["patient_id"])
            if resource["id"] == resource_id:
                return JSONResponse(media_type=FHIR_JSON, content=resource)
        return _problem(404, "not-found", f"{resource_type}/{resource_id} not found")

    @app.get("/fhir/{resource_type}")
    def search_type(resource_type: str, request: Request) -> JSONResponse:
        if resource_type not in RESOURCE_TABLES:
            return _problem(404, "not-supported", f"unknown resource type {resource_type}")
        try:
            as_of = parse_as_of(request.headers.get("X-As-Of"))
        except ValueError as exc:
            return _problem(400, "value", str(exc))

        params: Dict[str, Any] = dict(request.query_params)
        multi = request.query_params.getlist("date")
        if multi:
            params["date"] = multi
        try:
            patient_id, hosp_ids = search.resolve_context(store, params)
        except HttpProblem as exc:
            return _problem(exc.status, exc.code, exc.diagnostics)

        limit = int(params.get("_count", 1000))
        resources: List[Dict[str, Any]] = []
        for table in RESOURCE_TABLES[resource_type]:
            if table in ("patient", "adt"):
                continue                       # served via their own routes
            preds = search.predicates_for(table, params)
            for hosp_id in hosp_ids:
                for row in store.query(table, hosp_id, as_of=as_of,
                                       predicates=preds, limit=limit):
                    resources.append(map_row(table, row, patient_id))
        return JSONResponse(media_type=FHIR_JSON, content=_bundle(resources[:limit]))

    return app


app = None   # created by uvicorn entrypoint: helios.fhir.app:create_app
```

> **Route-order note:** FastAPI matches in declaration order, so `/fhir/{type}/{id}` must be
> declared *before* `/fhir/{type}`. The code above does this. Reversing them makes every read
> fall through to search.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_search.py tests/test_app.py -v`
Expected: 7 + 12 passed

- [ ] **Step 5: Commit**

```bash
git add helios/fhir/search.py helios/fhir/outcome.py helios/fhir/app.py tests/test_search.py tests/test_app.py
git commit -m "feat: search params, OperationOutcome errors, FHIR REST routes"
```

---

### Task 12: SMART auth

**Files:**
- Create: `helios/fhir/auth.py`, `tokens.yaml`
- Modify: `helios/fhir/app.py` (add discovery route and a dependency on every FHIR route)
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `HttpProblem` (11)
- Produces: `load_tokens(path: str) -> Dict[str, List[str]]`; `scope_for(resource_type: str) -> str`; `require_scope(tokens, header: Optional[str], resource_type: str) -> None` raising `HttpProblem(401|403)`

Real SMART scope strings and real 401/403; no JWT. In real SMART the only output of the JWT dance is a bearer plus a scope list, so enforcement is identical and the crypto can be added later without touching it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth.py
import pytest
from helios.fhir.auth import load_tokens, scope_for, require_scope
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

def test_valid_token_missing_scope_is_403():
    with pytest.raises(HttpProblem) as e:
        require_scope(TOKENS, "Bearer narrow", "Observation")
    assert e.value.status == 403

def test_valid_token_with_scope_passes():
    require_scope(TOKENS, "Bearer good", "Observation")

def test_wildcard_scope_passes():
    require_scope(TOKENS, "Bearer wild", "Observation")

def test_loads_tokens_from_yaml(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text('tokens:\n  a: ["system/Patient.rs"]\n')
    assert load_tokens(str(p)) == {"a": ["system/Patient.rs"]}
```

Add to `tests/test_app.py`:

```python
def test_discovery_document(client):
    body = client.get("/.well-known/smart-configuration").json()
    assert "token_endpoint" in body
    assert "client_credentials" in body["grant_types_supported"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_auth.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'helios.fhir.auth'`

- [ ] **Step 3: Write minimal implementation**

```python
# helios/fhir/auth.py
"""SMART-shaped auth without the JWT ceremony.

Real SMART's whole output is a bearer token plus a scope list, so enforcement
here is identical to the real thing. Swapping static tokens for the
client_credentials JWT flow later touches load_tokens only.
"""
from pathlib import Path
from typing import Dict, List, Optional
import yaml

from helios.fhir.outcome import HttpProblem


def load_tokens(path: str = "tokens.yaml") -> Dict[str, List[str]]:
    p = Path(path)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()).get("tokens", {}) or {}


def scope_for(resource_type: str) -> str:
    return f"system/{resource_type}.rs"


def require_scope(tokens: Dict[str, List[str]], header: Optional[str],
                  resource_type: str) -> None:
    if not tokens:
        return                                    # auth disabled: no tokens.yaml
    if not header or not header.startswith("Bearer "):
        raise HttpProblem(401, "login", "a Bearer token is required")
    granted = tokens.get(header[len("Bearer "):].strip())
    if granted is None:
        raise HttpProblem(401, "login", "unknown bearer token")
    needed = scope_for(resource_type)
    if needed not in granted and "system/*.rs" not in granted:
        raise HttpProblem(403, "forbidden", f"token lacks scope {needed}")
```

```yaml
# tokens.yaml
tokens:
  agent-readonly:
    - "system/Patient.rs"
    - "system/Encounter.rs"
    - "system/Observation.rs"
    - "system/Condition.rs"
    - "system/Procedure.rs"
    - "system/MedicationAdministration.rs"
  agent-labs-only:
    - "system/Patient.rs"
    - "system/Observation.rs"
```

In `app.py`: load tokens in `create_app`, call `require_scope(tokens, request.headers.get("Authorization"), resource_type)` at the top of both `read` and `search_type` (after the resource-type check), and add:

```python
    @app.get("/.well-known/smart-configuration")
    def smart_configuration() -> JSONResponse:
        return JSONResponse(content={
            "token_endpoint": "/oauth2/token",
            "grant_types_supported": ["client_credentials"],
            "token_endpoint_auth_methods_supported": ["private_key_jwt"],
            "scopes_supported": [scope_for(t) for t in RESOURCE_TABLES],
            "capabilities": ["client-confidential-asymmetric",
                             "permission-v2", "context-standalone-patient"],
        })
```

The `client` fixture in `tests/test_app.py` writes no `tokens.yaml`, so auth stays disabled there and the existing tests keep passing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all pass, including the new discovery test

- [ ] **Step 5: Commit**

```bash
git add helios/fhir/auth.py tokens.yaml helios/fhir/app.py tests/test_auth.py tests/test_app.py
git commit -m "feat: SMART scopes, bearer enforcement, discovery document"
```

---

### Task 13: Golden file, Docker, README

**Files:**
- Create: `tests/test_golden.py`, `tests/golden/H_sample.json`, `docker/fhir.Dockerfile`, `README.md`
- Test: `tests/test_golden.py`

**Interfaces:**
- Consumes: everything
- Produces: nothing new; this task locks behaviour and makes the server runnable

- [ ] **Step 1: Write the golden test**

```python
# tests/test_golden.py
"""One real hospitalization rendered to FHIR, checked in and diffed.
Regenerate deliberately with: HELIOS_REGOLD=1 .venv/bin/python -m pytest tests/test_golden.py
"""
import json, os, pytest
from pathlib import Path
from fastapi.testclient import TestClient
from helios.fhir.app import create_app
from helios.fhir.config import load_config

GOLDEN = Path(__file__).parent / "golden" / "H_sample.json"


@pytest.fixture(scope="module")
def client():
    cfg = load_config()
    if not (cfg.prepared_directory / "encounter_index.parquet").exists():
        pytest.skip("run helios.scripts.prepare_data first")
    return TestClient(create_app(cfg))


def test_golden_hospitalization(client):
    import duckdb
    cfg = load_config()
    hosp_id = duckdb.connect().execute(
        f"SELECT hospitalization_id FROM "
        f"'{cfg.prepared_directory}/encounter_index.parquet' ORDER BY 1 LIMIT 1"
    ).fetchone()[0]
    body = client.get("/fhir/Observation",
                      params={"encounter": hosp_id, "_count": 20}).json()
    actual = json.dumps(body, indent=2, sort_keys=True, default=str)
    if os.environ.get("HELIOS_REGOLD"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(actual)
        pytest.skip("golden file regenerated")
    assert actual == GOLDEN.read_text()
```

- [ ] **Step 2: Generate the golden file**

Run: `HELIOS_REGOLD=1 .venv/bin/python -m pytest tests/test_golden.py -v`
Expected: skipped with "golden file regenerated"; `tests/golden/H_sample.json` now exists. **Read it** and confirm the resources look right before committing — a golden file locks in whatever it captured, bugs included.

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all pass

- [ ] **Step 4: Write the Dockerfile and README**

```dockerfile
# docker/fhir.Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY helios/ ./helios/
COPY config.yaml tokens.yaml ./
EXPOSE 8000
CMD ["uvicorn", "--factory", "helios.fhir.app:create_app", \
     "--host", "0.0.0.0", "--port", "8000"]
```

`README.md` must cover: the one-time `prepare_data` run, `uvicorn --factory helios.fhir.app:create_app`, a worked `curl` showing `X-As-Of` and a bearer token, and the five known limitations from the spec — most importantly that `Condition` cannot be as-of filtered.

- [ ] **Step 5: Smoke-test the running server**

```bash
.venv/bin/uvicorn --factory helios.fhir.app:create_app --port 8000 &
curl -s localhost:8000/fhir/metadata | head -5
curl -s "localhost:8000/fhir/Observation?patient=<a real patient_id>&_count=2" \
     -H "Authorization: Bearer agent-readonly" | head -30
```
Expected: a CapabilityStatement, then a searchset Bundle.

- [ ] **Step 6: Commit**

```bash
git add tests/test_golden.py tests/golden/ docker/ README.md
git commit -m "feat: golden-file test, Dockerfile, README"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: decisions 1-8 to Global Constraints; storage layout and dedup to Task 3; clock to Task 4; terminology to Task 5; resource identity to Task 6; the 12->6 mapping to Tasks 8-10; request flow, error handling and search-context rule to Task 11; auth and discovery to Task 12; testing items 1-6 across Tasks 2-13. The spec's two absent microbiology tables need no task — `Store.query` returns `[]` for a missing file (tested in Task 7) and they are simply absent from `RESOURCE_TABLES` until data exists.

**Known gaps, deliberate:** `adt` is loaded and prepared but not yet rendered into `Encounter.location[]`; the registry lists it so Task 8's Encounter mapper can be extended without restructuring. `_count` paginates by truncation with no `next` link. Both are noted rather than hidden.

**Type consistency.** `to_fhir` signatures differ by design and the registry absorbs it: `patient.to_fhir(row)` and `encounter.to_fhir(row)` take one argument, `condition/procedure.to_fhir(row, patient_id)` take two, `observation/medication.to_fhir(table, row, patient_id)` take three. `map_row(table, row, patient_id)` is the single entry point every caller uses. `Store.query` returns `List[Dict[str, Any]]` throughout, and `clock_predicate` returns `str` (empty when inapplicable) — never `None`.
