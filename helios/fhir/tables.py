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
    # no timestamp column exists; Condition cannot be as-of filtered
    "hospital_diagnosis": _t("hospital_diagnosis", None, dedup=True),
    "patient_procedures": _t("patient_procedures", "procedure_billed_dttm", dedup=True),
}

IN_SCOPE: List[str] = list(TABLES)
