"""DuckDB over prepared parquet, plus the in-memory encounter index.

Every fact query is scoped to one hospitalization_id. That is what makes
row-group pruning effective on files sorted by that column, and it is why
searches require patient context.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

import duckdb

from helios.fhir.clock import clock_predicate
from helios.fhir.config import Config
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
            return []  # declared but absent
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

    def _rows(self, sql: str,
              params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        cur = self._con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
