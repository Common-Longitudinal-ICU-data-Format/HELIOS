"""FHIR OperationOutcome. Every error body is one of these: an agent parsing a
plain-text 500 learns nothing, an OperationOutcome tells it what to do next.
"""
from typing import Any, Dict


class HttpProblem(Exception):
    def __init__(self, status: int, code: str, diagnostics: str) -> None:
        super().__init__(diagnostics)
        self.status, self.code, self.diagnostics = status, code, diagnostics


def operation_outcome(severity: str, code: str, diagnostics: str) -> Dict[str, Any]:
    return {"resourceType": "OperationOutcome",
            "issue": [{"severity": severity, "code": code,
                       "details": {"text": diagnostics}, "diagnostics": diagnostics}]}
