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
from helios.fhir.tables import IN_SCOPE, TABLES


def prepare(config: Config, tables: Optional[List[str]] = None) -> Dict[str, int]:
    targets = tables if tables is not None else IN_SCOPE
    config.prepared_directory.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    counts: Dict[str, int] = {}

    for name in targets:
        spec = TABLES[name]
        src = config.data_directory / f"clif_{name}.{config.filetype}"
        if not src.exists():
            continue  # declared but absent, e.g. microbiology
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
    discharge = (
        "discharge_dttm" if "discharge_dttm" in cols
        else "NULL::TIMESTAMP AS discharge_dttm"
    )
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
