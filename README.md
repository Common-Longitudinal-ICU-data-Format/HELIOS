# HELIOS

Healthcare Environment for Learning, Inference, Observation Simulation.

**`helios-fhir`** serves [CLIF](https://clif-consortium.org/) ICU parquet as a read-only
FHIR R4B API, translated per request. A separate `helios-mcp` layer (not built yet) will
expose it to agents as tools.

- Spec: [`docs/superpowers/specs/2026-09-03-helios-fhir-mcp-design.md`](docs/superpowers/specs/2026-09-03-helios-fhir-mcp-design.md)
- Plan: [`docs/superpowers/plans/2026-09-06-helios-fhir.md`](docs/superpowers/plans/2026-09-06-helios-fhir.md)
- Overview page: `docs/plan.html`

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# point config.yaml at your CLIF export, then rewrite it once (~75s for MIMIC)
.venv/bin/python -m helios.scripts.prepare_data

.venv/bin/uvicorn --factory helios.fhir.app:create_app --port 8000
```

```bash
curl -s localhost:8000/fhir/metadata | jq .fhirVersion

curl -s "localhost:8000/fhir/Observation?encounter=20000019&code=creatinine&_count=3" \
     -H "Authorization: Bearer agent-readonly" \
     -H "X-As-Of: 2159-03-21T12:00:00-05:00" | jq '.total'
```

## How it works

Nothing is materialised. Each request becomes one DuckDB query against parquet sorted by
`hospitalization_id`, and the matched rows are translated to FHIR in memory and discarded.
Changing a mapping is an edit and a restart — there is no second copy of the data to rebuild
or to drift.

```
request ──► auth (scopes) ──► encounter index ──► DuckDB ──► mappers ──► Bundle
                                (in memory)      (pruned)              discarded
```

`prepare_data` sorts by `hospitalization_id` because that is what makes Parquet row-group
statistics prunable. Measured on this dataset, labs at cohort 100 reads 14.3 MB sorted vs
61.5 MB unsorted; hive-partitioning by year reads 62.5 MB, i.e. no better than unsorted,
because an ID predicate carries no year information to prune on.

12 CLIF tables map to 6 resource types: `Patient`, `Encounter`, `Observation`, `Condition`,
`Procedure`, `MedicationAdministration`.

## The as-of clock

`X-As-Of` filters every timestamp to `<= T`, so an agent reasoning at ICU hour 24 cannot see
hour 72. Each table declares its own clock column (`recorded_dttm`, `lab_result_dttm`,
`admin_dttm`, …).

**Dates are native MIMIC time — stays sit in the 2100s.** Anything supplying "now" must speak
MIMIC time. A prompt saying "it is 2023-11-13" against a patient admitted 2159-03-21 returns
empty bundles, correctly. Pin clocks relative to `admission_dttm`.

## Auth

Static bearer tokens carrying real SMART scope strings (`tokens.yaml`), a real
`/.well-known/smart-configuration`, and real `401`/`403`. **Delete `tokens.yaml` to disable
auth.** In real SMART the only output of the JWT/JWKS flow is a bearer plus a scope list, so
swapping the static tokens for `client_credentials` later touches `load_tokens` alone.

## Known limitations

1. **`Condition` cannot be as-of filtered.** `hospital_diagnosis` has no timestamp column, so
   discharge diagnoses are visible to an agent reasoning at hour 24. A real look-ahead leak;
   scope it in the harness.
2. **`vital_category` is lossy.** Arterial-line and non-invasive cuff share `sbp`/`dbp`/`map`
   — 1,416,321 rows affected. `vital_name` is preserved in `Observation.method` to recover the
   distinction, but an agent filtering on category alone still mixes measurement methods.
3. **LOINC is 54 hand-curated rows** (9 vitals, 45 labs) because `lab_loinc_code` is 0%
   populated in this export. **Needs clinical review before production use.** Medications and
   assessments carry CLIF-native codes only — no RxNorm.
4. **Searches require `patient` or `encounter`.** Without one there is nothing to prune on.
   Matches Epic, but is a deviation from unrestricted FHIR search.
5. **Row counts differ from raw parquet.** `patient_procedures`, `hospital_diagnosis` and
   `position` are deduplicated at prepare time (−9,310 / −199 / −2 byte-identical rows).
6. **FHIR R4B (4.3.0), not R4 (4.0.1).** `fhir.resources` 8.x ships R4B and STU3 and defaults
   to R5; there is no plain R4. R4B is the closest available to the spec's R4.
7. **Read-by-id is O(n) within an encounter.** It re-queries the table and maps rows until an
   id matches. Correct, but slow on large encounters; search is the fast path.
8. **`adt` is prepared but not yet rendered** into `Encounter.location[]`.
9. **Microbiology is absent from this export.** The tables are declared in CLIF Beta but carry
   no data here, so those endpoints are not registered.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

The load-bearing ones are the **look-ahead tests** (`test_store.py`,
`test_app.py::test_as_of_header_excludes_later_rows`) — if as-of filtering regresses, every
evaluation built on this server is silently invalid.
