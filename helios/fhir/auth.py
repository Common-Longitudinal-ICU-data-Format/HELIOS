"""SMART-shaped auth without the JWT ceremony.

Real SMART's whole output is a bearer token plus a scope list, so enforcement
here is identical to the real thing. Swapping static tokens for the
client_credentials JWT flow later touches load_tokens only.
"""
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from helios.fhir.outcome import HttpProblem

WILDCARD = "system/*.rs"


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
        return  # auth disabled: no tokens.yaml present
    if not header or not header.startswith("Bearer "):
        raise HttpProblem(401, "login", "a Bearer token is required")
    granted = tokens.get(header[len("Bearer "):].strip())
    if granted is None:
        raise HttpProblem(401, "login", "unknown bearer token")
    needed = scope_for(resource_type)
    if needed not in granted and WILDCARD not in granted:
        raise HttpProblem(403, "forbidden", f"token lacks scope {needed}")
