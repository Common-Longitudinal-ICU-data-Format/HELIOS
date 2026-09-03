# HELIOS: a FHIR facade over CLIF, with an MCP layer for agents

**Date:** 2026-09-03
**Status:** approved design, ready for implementation planning

## Problem

CLIF (Common Longitudinal ICU data Format) stores ICU data as columnar parquet: 15 beta
tables, one row per measurement. AI agents cannot consume that. They consume FHIR, because
that is what every EHR exposes and what agent tooling is built against.

HELIOS makes CLIF readable as FHIR, then puts an MCP layer on top so an agent can ask for
a patient's data as tool calls instead of HTTP.

Two independently runnable services:

- **`helios-fhir`** — a read-only FHIR R4 server over CLIF parquet. Runs standalone. Knows
  nothing about agents or MCP.
- **`helios-mcp`** — an MCP server exposing tools to agents. Speaks only FHIR REST to
  `helios-fhir`. Holds no data and never touches parquet.

The dependency runs one way. `helios-fhir` must be usable with `curl` and with any
off-the-shelf FHIR client, with `helios-mcp` deleted.

## Decisions

Settled during design. Each is a constraint on implementation, not a suggestion.

| # | Decision | Consequence |
|---|---|---|
| 1 | **Read-only.** No POST/PUT/PATCH/DELETE. | Parquet is never mutated. No reset logic, no session state, reproducible by construction. Write verbs return `405`. |
| 2 | **CLIF Beta tables only**, minus `code_status`. | 12 tables in scope. `crrt_therapy` and `ecmo_mcs` are excluded although the files exist. |
| 3 | **Hybrid terminology.** | Standard codes where the data already carries them (ICD, CPT); a curated LOINC map for vitals and labs; CLIF-native local CodeSystems elsewhere. |
| 4 | **Static bearer tokens carrying real SMART scopes.** | Real `/.well-known/smart-configuration`, real scope enforcement, real `401`/`403`. No JWT/JWKS ceremony. Localhost-only simulation. |
| 5 | **Per-request as-of clock.** | `X-As-Of` filters every `*_dttm` to `<= T`. Prevents look-ahead leakage in evaluations. |
| 6 | **Native MIMIC dates, passed through.** | No date shifting. Stays remain in the 2100s. No offset table, no timestamp transforms. |
| 7 | **Lazy translate-on-read.** | FHIR resources are projections built per request and discarded. Nothing materialised. |
| 8 | **Searches require patient or encounter context.** | Keeps every query prunable. Matches Epic, which mandates `patient` on nearly every search. |

## Data

Source: `/Users/sudo_sage/Downloads/work/clif_m` (MIMIC-CLIF). Read-only; nothing outside
this directory is used.

```
patient                    364,627      hospitalization              546,028
adt                      1,458,408      hospital_diagnosis         6,364,488
labs                    44,880,526      vitals                    55,525,580
patient_assessments     19,389,145      medication_admin_cont      7,564,662
respiratory_support      2,148,372      medication_admin_int       3,252,010
position                 3,157,996      patient_procedures         1,045,729
                                        ~145M rows, 1.1 GB compressed
```

Declared Beta but absent from this export: `microbiology_culture`,
`microbiology_susceptibility`. Their endpoints are declared and return empty `searchset`
bundles with `total: 0` — an agent must be able to distinguish "no cultures" from "tool
broken".

### Coding coverage, measured

- `hospital_diagnosis.diagnosis_code` — **100% populated**. `diagnosis_code_format` is
  `ICD10CM` (3.46M) or `ICD9CM` (2.91M).
- `patient_procedures.procedure_code` — **100% populated**. Formats: `ICD9` (469k),
  `ICD10PCS` (390k), `CPT` (116k), `HCPCS` (70k).
- `labs.lab_loinc_code` — **0% populated**. The column exists and is entirely null. LOINC
  for labs must be curated, not read.

Curation required: **54 rows total** — 9 vital categories and 45 lab categories present in
this dataset, mapped to LOINC by hand. Medications (178 categories) and assessments (72)
stay CLIF-native per decision 3.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  helios-mcp                        container 2, deletable        │
│  MCP tools over FHIR REST + bearer token. No parquet access.     │
└───────────────────────────┬──────────────────────────────────────┘
                            │  HTTP   Authorization: Bearer <token>
                            │         X-As-Of: 2110-03-15T08:00:00Z
┌───────────────────────────▼──────────────────────────────────────┐
│  helios-fhir                       container 1, runs standalone  │
│                                                                  │
│   app.py       routes: /fhir/{Type}, /fhir/{Type}/{id},          │
│                /fhir/metadata, /.well-known/smart-configuration  │
│   auth.py      bearer -> scopes; enforce system/{Type}.rs        │
│   clock.py     parse X-As-Of -> a single SQL predicate           │
│   search.py    FHIR search params -> SQL predicates              │
│   store.py     DuckDB over sorted parquet + encounter index      │
│   mappers/     one module per resource type                      │
│   codes/       vitals_loinc.csv, labs_loinc.csv, systems.py      │
└───────────────────────────┬──────────────────────────────────────┘
                            │ read-only, never mutated
                 /Users/sudo_sage/Downloads/work/clif_m/*.parquet
```

### Why lazy, not materialised

Materialising 145M CLIF rows produces ~145M FHIR resources. FHIR JSON runs 10-50x the size
of columnar parquet, so 1.1 GB becomes tens of GB plus a multi-hour build, rebuilt on every
mapping change. Both hard constraints make it worse rather than better: the as-of clock
means the useful unit is "rows before T", which a resource store does not help with, and
decision 1 removes the writes that materialisation exists to serve.

Lazy translation makes the FHIR layer a function, not a copy. A mapping change is an edit
and a restart. Parquet and FHIR cannot drift, because there is only one copy of the data.

### Storage layout

The 12 tables are re-sorted by `hospitalization_id` once, offline. This is not a guess: the
existing benchmark in `clif_partition_benchmark/results/report.md` measured, for labs at
cohort size 100, **14.3 MB read when sorted by `hospitalization_id` vs 61.5 MB unsorted and
62.5 MB hive-partitioned by year**.

The mechanism is row-group min/max statistics. Sorting by `hospitalization_id` makes those
statistics tight and non-overlapping so a reader skips row groups without decompressing
them. Partitioning by year cannot help, because an ID predicate carries no year information
to prune on. A FHIR server's characteristic query is a single hospitalization — the regime
where sorting wins and partitioning does not.

An **encounter index** is built alongside: 546,028 rows of
`(hospitalization_id, patient_id, admission_dttm, discharge_dttm)`, roughly 15 MB, held in
memory. It answers identity resolution and stay bounds without touching a fact table.

## Resource mapping

12 tables -> 6 resource types.

| CLIF table | FHIR resource | Coding |
|---|---|---|
| `patient` | `Patient` | US Core race/ethnicity extensions |
| `hospitalization` + `adt` | `Encounter` (+ `.location[]`) | CLIF-native class |
| `vitals` | `Observation` (`vital-signs`) | **LOINC**, 9-row map |
| `labs` | `Observation` (`laboratory`) | **LOINC**, 45-row map |
| `patient_assessments` | `Observation` (`survey`) | CLIF-native |
| `respiratory_support` | `Observation` | CLIF-native |
| `position` | `Observation` | CLIF-native |
| `medication_admin_continuous` | `MedicationAdministration` | CLIF-native |
| `medication_admin_intermittent` | `MedicationAdministration` | CLIF-native |
| `hospital_diagnosis` | `Condition` | **ICD-10-CM / ICD-9-CM**, free |
| `patient_procedures` | `Procedure` | **CPT / ICD10PCS / HCPCS**, free |
| *(absent)* `microbiology_*` | `Observation` | declared, empty bundles |

CLIF-native codes use local CodeSystem URIs of the form
`http://clif-consortium.org/fhir/CodeSystem/{table}-category`, with the CLIF category string
as the code and `*_name` as `display` when present.

### Resource identity

`Patient.id` is `patient_id`; `Encounter.id` is `hospitalization_id`. Both come straight
from CLIF.

Fact rows have no natural key, so **resource ids are content-addressed rather than
surrogate**: `{table}-{hospitalization_id}-{epoch_seconds}-{category}`. The id encodes
enough to re-find the source row, so `GET /fhir/Observation/{id}` is itself a lazy lookup
with no id-to-row index. Ids are stable across restarts because they are derived from the
data, not assigned.

## Request flow

`GET /fhir/Observation?patient=P123&code=creatinine` with `X-As-Of: 2110-03-15T08:00:00Z`

```
 1  auth.py     bearer -> scopes; require system/Observation.rs
 2  search.py   reject if no patient/encounter context (400 + OperationOutcome)
 3  store.py    encounter index point-lookup -> hospitalization_id, stay bounds
 4  store.py    one DuckDB query against the sorted parquet:
                  WHERE hospitalization_id = 'H456'
                    AND lab_category      = 'creatinine'
                    AND lab_result_dttm  <= '2110-03-15 08:00:00'
 5  DuckDB      column pruning  -> 5 of 14 columns
                row-group pruning -> min/max on hospitalization_id
                -> ~40 rows out of 44,880,526
 6  mappers/observation.py   rows -> FHIR Observation dicts
 7  app.py      wrap in searchset Bundle, serialise, respond
 8  ─────────── resources garbage collected, nothing persisted ───────────
```

### The clock

`clock.py` has one job: parse `X-As-Of` and return a SQL predicate. No timestamp
transformation exists anywhere in the server (decision 6).

When `X-As-Of` is absent the default is the encounter's `discharge_dttm` — the whole stay.
Because dates are native MIMIC, any harness that pins a clock must express it in MIMIC time,
relative to `admission_dttm`. A prompt saying "it is 2023-11-13" against a patient admitted
2110-03-14 returns empty bundles, correctly. This is a note for the MCP and harness layers.

Each table declares which column the clock applies to, since they differ:

| table | clock column |
|---|---|
| `vitals`, `patient_assessments`, `position`, `respiratory_support` | `recorded_dttm` |
| `labs` | `lab_result_dttm` |
| `medication_admin_continuous`, `medication_admin_intermittent` | `admin_dttm` |
| `adt` | `in_dttm` |
| `patient_procedures` | `procedure_billed_dttm` |
| `hospitalization` | `admission_dttm` |
| `hospital_diagnosis` | *(none — no timestamp; scoped by encounter only)* |

`hospital_diagnosis` carries no timestamp, so `Condition` cannot be as-of filtered. This is
a real look-ahead hole and must be documented rather than hidden: discharge diagnoses are
knowable only at discharge, so exposing them to an agent reasoning at hour 24 leaks the
future. Mitigation is a scope decision for the harness, not a server fix.

## Auth

`/.well-known/smart-configuration` advertises `token_endpoint`, supported scopes, and
`client_credentials`. Tokens come from a `tokens.yaml` mapping opaque strings to scope
lists:

```yaml
tokens:
  agent-readonly:   [ "system/Patient.rs", "system/Observation.rs", "system/Encounter.rs" ]
  agent-labs-only:  [ "system/Patient.rs", "system/Observation.rs" ]
```

Enforcement is real: missing or unknown token -> `401` with `WWW-Authenticate`; valid token
lacking the scope -> `403` with an `OperationOutcome`. The JWT/JWKS flow can replace token
lookup later without touching enforcement, because enforcement only ever sees a scope list.

## Error handling

| Condition | Response |
|---|---|
| Missing/unknown bearer | `401` + `WWW-Authenticate`, `OperationOutcome` |
| Valid token, missing scope | `403` + `OperationOutcome` |
| Search without patient/encounter context | `400` + `OperationOutcome` (`code: required`) |
| Malformed `X-As-Of` | `400` + `OperationOutcome` |
| Unknown resource type | `404` + `OperationOutcome` |
| Read by unknown id | `404` + `OperationOutcome` |
| Search matching nothing | `200` + empty `searchset` Bundle, `total: 0` |
| Microbiology endpoints | `200` + empty Bundle (never `404`) |
| Any write verb | `405`, and absent from the CapabilityStatement |

Every error body is a real `OperationOutcome`. An agent parsing a plain-text 500 learns
nothing; an `OperationOutcome` tells it whether to retry, narrow, or give up.

## Project layout

```
helios/
  fhir/
    app.py  auth.py  clock.py  search.py  store.py
    mappers/  __init__.py  patient.py  encounter.py  observation.py
              condition.py  procedure.py  medication.py
    codes/    vitals_loinc.csv  labs_loinc.csv  systems.py
  mcp/
    server.py
  scripts/
    prepare_data.py
  tests/
  docker/
    fhir.Dockerfile  mcp.Dockerfile
  config.yaml
```

`config.yaml` reuses the clifpy config shape already in use:

```yaml
site_name: mimic
data_directory: /Users/sudo_sage/Downloads/work/clif_m
filetype: parquet
timezone: US/Eastern
```

Dependencies are deliberately few: `fastapi`, `uvicorn`, `duckdb`, `fhir.resources`,
`pyyaml`. **clifpy is not a dependency.** It loads whole tables into pandas with filters,
which is the wrong shape for per-request serving; DuckDB queries the parquet directly. CLIF
schemas are still the contract, they are just not read through clifpy.

`mappers/__init__.py` is a registry mapping resource type to module, so adding the
microbiology tables later is a registry entry plus one module.

## Testing

The `fhir.resources` pydantic models give schema validation free — constructing a resource
that fails FHIR validation raises. That covers structure, so tests target semantics:

1. **Mapper unit tests** — a CLIF row dict in, a valid FHIR resource out, asserting the
   coding system and code are right. One per mapper.
2. **Look-ahead tests (highest value)** — for a known hospitalization, assert that a query
   with `X-As-Of` set mid-stay returns no row whose clock column is after `T`. Run per table,
   since each has its own clock column. This is the test that protects the benchmark's
   validity, and it is the one most likely to regress silently.
3. **Scope enforcement tests** — each route returns `401` with no token, `403` with a token
   lacking the scope, `200` with the right scope.
4. **Contract tests** — search without patient context is `400`; microbiology search is `200`
   with an empty bundle; every write verb is `405`.
5. **Golden-file test** — one real hospitalization rendered to FHIR, checked in, diffed. Makes
   unintended mapping changes visible in review.

## Build order

| Phase | Deliverable |
|---|---|
| 0 | `prepare_data.py` — re-sort 12 tables by `hospitalization_id`, build encounter index |
| 1 | `store.py`, `clock.py`, `Patient` + `Encounter` mappers, routes. No auth |
| 2 | `Observation` mappers (vitals, labs, assessments, respiratory, position) + the 54 LOINC rows |
| 3 | `Condition`, `Procedure`, `MedicationAdministration` |
| 4 | `auth.py`, discovery document, CapabilityStatement |
| 5 | `helios-mcp` |

Phases 1-4 leave `helios-fhir` independently runnable and testable. Phase 5 starts only
after the FHIR server is complete, which is what keeps the dependency one-way.

## Known limitations

1. `Condition` cannot be as-of filtered — `hospital_diagnosis` has no timestamp. Documented
   above; a real look-ahead hole.
2. LOINC coverage is 54 curated rows. Medications and assessments carry CLIF-native codes
   only, so an agent expecting RxNorm will not find it.
3. Searches without patient context are refused rather than served. Deliberate, and it
   matches Epic, but it is a genuine deviation from unrestricted FHIR search.
4. Microbiology endpoints exist but this export has no data behind them.
5. Dates are in the 2100s. Anything supplying "now" must speak MIMIC time.
