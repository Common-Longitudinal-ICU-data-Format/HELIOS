"""Terminology URIs and the curated LOINC maps.

labs.lab_loinc_code is 0% populated in this export, so lab LOINC is curated
here rather than read from the data. These 54 mappings were written by hand and
need clinical review before production use - the specimen-dependent ones
(so2_central_venous, bilirubin_unconjugated, lactate serum vs blood) most of all.
"""
import csv
from pathlib import Path
from typing import Dict, Optional, Tuple

LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
SNOMED = "http://snomed.info/sct"

_CODE_SYSTEMS = {
    "ICD10CM": "http://hl7.org/fhir/sid/icd-10-cm",
    "ICD9CM": "http://hl7.org/fhir/sid/icd-9-cm",
    "ICD9": "http://hl7.org/fhir/sid/icd-9-cm",
    "ICD10PCS": "http://www.cms.gov/Medicare/Coding/ICD10",
    "CPT": "http://www.ama-assn.org/go/cpt",
    "HCPCS": "urn:oid:2.16.840.1.113883.6.285",
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


def loinc_for(kind: str, category: Optional[str]) -> Optional[Tuple[str, str, str]]:
    if category is None:
        return None
    return _MAPS[kind].get(category)
