"""Five CLIF tables -> FHIR Observation.

CLIF's *_category is lossy: vital_category collapses arterial-line and
non-invasive cuff into one 'map'. The distinguishing information lives in
*_name, so it is carried in Observation.method and must not be dropped.
"""
from typing import Any, Dict, List, Optional

from fhir.resources.R4B.observation import Observation

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
    if table in ("vitals", "labs"):
        mapped = loinc_for(table, category)
        if mapped:
            code, display, _unit = mapped
            return {"system": LOINC, "code": code, "display": display}
    return {"system": clif_system(table), "code": category or "unknown"}


def _quantity(value, unit: Optional[str]) -> Dict[str, Any]:
    if value is None:
        return {}
    q: Dict[str, Any] = {"value": float(value)}
    if unit:
        q.update({"unit": unit, "system": UCUM, "code": unit})
    return {"valueQuantity": q}


def _value(table: str, row: Dict[str, Any]) -> Dict[str, Any]:
    if table == "vitals":
        mapped = loinc_for("vitals", row.get("vital_category"))
        return _quantity(row.get("vital_value"), mapped[2] if mapped else None)
    if table == "labs":
        numeric = row.get("lab_value_numeric")
        if numeric is not None:
            return _quantity(numeric, row.get("reference_unit"))
        return {"valueString": str(row["lab_value"])} if row.get("lab_value") else {}
    if table == "patient_assessments":
        if row.get("numerical_value") is not None:
            return _quantity(row["numerical_value"], None)
        for key in ("categorical_value", "text_value"):
            if row.get(key):
                return {"valueString": str(row[key])}
        return {}
    if table == "position":
        return {"valueString": row.get("position_category") or "unknown"}
    return {}


def _components(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"code": {"text": key}, "valueQuantity": {"value": float(row[key])}}
            for key in _RESP_COMPONENTS if row.get(key) is not None]


def to_fhir(table: str, row: Dict[str, Any], patient_id: str) -> Dict[str, Any]:
    spec = TABLES[table]
    category = row.get(spec.category_column) if spec.category_column else None
    when = row.get(spec.clock_column) if spec.clock_column else None
    hosp_id = str(row["hospitalization_id"])

    resource: Dict[str, Any] = {
        "resourceType": "Observation",
        "id": make_id(table, row, hosp_id, when, category),
        "status": "final",
        "category": [{"coding": [
            {"system": _OBS_CATEGORY, "code": CATEGORY_BY_TABLE[table]}]}],
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
