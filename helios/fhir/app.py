"""The FHIR REST surface. Read-only: no write route exists."""
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from helios.fhir import search
from helios.fhir.auth import load_tokens, require_scope, scope_for
from helios.fhir.clock import parse_as_of
from helios.fhir.config import Config, load_config
from helios.fhir.ids import parse_id
from helios.fhir.mappers import RESOURCE_TABLES, map_row
from helios.fhir.mappers import patient as patient_mapper
from helios.fhir.outcome import HttpProblem, operation_outcome
from helios.fhir.store import Store
from helios.fhir.times import configure as configure_timezone

FHIR_JSON = "application/fhir+json"
FHIR_VERSION = "4.3.0"  # R4B: fhir.resources 8.x ships no plain R4

# Served through their own routes rather than as search results.
_NOT_SEARCHABLE = {"patient", "adt"}


def _bundle(resources: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"resourceType": "Bundle", "type": "searchset",
            "total": len(resources),
            "entry": [{"resource": r} for r in resources]}


def _problem(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, media_type=FHIR_JSON,
                        content=operation_outcome("error", code, message))


def create_app(config: Optional[Config] = None,
               tokens_path: str = "tokens.yaml") -> FastAPI:
    cfg = config or load_config()
    configure_timezone(cfg.timezone)
    store = Store(cfg)
    # No tokens file means auth is off - useful for tests and local pokes.
    tokens = load_tokens(tokens_path)
    app = FastAPI(title="helios-fhir", docs_url=None, redoc_url=None)

    def _guard(request: Request, resource_type: str):
        """Auth + as-of. Returns (as_of, None) or (None, error response)."""
        try:
            require_scope(tokens, request.headers.get("Authorization"), resource_type)
            return parse_as_of(request.headers.get("X-As-Of")), None
        except HttpProblem as exc:
            return None, _problem(exc.status, exc.code, exc.diagnostics)
        except ValueError as exc:
            return None, _problem(400, "value", str(exc))

    @app.get("/.well-known/smart-configuration")
    def smart_configuration() -> JSONResponse:
        return JSONResponse(content={
            "token_endpoint": "/oauth2/token",
            "grant_types_supported": ["client_credentials"],
            "token_endpoint_auth_methods_supported": ["private_key_jwt"],
            "scopes_supported": [scope_for(t) for t in RESOURCE_TABLES],
            "capabilities": ["client-confidential-asymmetric", "permission-v2"],
        })

    @app.get("/fhir/metadata")
    def capability() -> JSONResponse:
        resources = [{"type": t,
                      "interaction": [{"code": "read"}, {"code": "search-type"}]}
                     for t in RESOURCE_TABLES]
        return JSONResponse(media_type=FHIR_JSON, content={
            "resourceType": "CapabilityStatement", "status": "active",
            "date": "2026-09-06", "kind": "instance",
            "fhirVersion": FHIR_VERSION, "format": [FHIR_JSON],
            "rest": [{"mode": "server", "resource": resources}]})

    # Declared before the search route: FastAPI matches in declaration order.
    @app.get("/fhir/{resource_type}/{resource_id}")
    def read(resource_type: str, resource_id: str, request: Request) -> JSONResponse:
        if resource_type not in RESOURCE_TABLES:
            return _problem(404, "not-supported",
                            f"unknown resource type {resource_type}")
        as_of, error = _guard(request, resource_type)
        if error is not None:
            return error

        if resource_type == "Patient":
            row = store.patient(resource_id)
            if row is None:
                return _problem(404, "not-found", f"Patient/{resource_id} not found")
            return JSONResponse(media_type=FHIR_JSON,
                                content=patient_mapper.to_fhir(row))

        if resource_type == "Encounter":
            row = store.encounter(resource_id)
            if row is None:
                return _problem(404, "not-found", f"Encounter/{resource_id} not found")
            return JSONResponse(media_type=FHIR_JSON,
                                content=map_row("hospitalization", row,
                                                row["patient_id"]))

        # Fact resources: the id encodes where the row lives, so re-find it.
        try:
            parts = parse_id(resource_id)
        except ValueError:
            return _problem(404, "not-found", f"malformed id {resource_id}")
        enc = store.encounter(parts["hosp_id"])
        if enc is None:
            return _problem(404, "not-found",
                            f"{resource_type}/{resource_id} not found")
        for row in store.query(parts["table"], parts["hosp_id"],
                               as_of=as_of, limit=100000):
            resource = map_row(parts["table"], row, enc["patient_id"])
            if resource["id"] == resource_id:
                return JSONResponse(media_type=FHIR_JSON, content=resource)
        return _problem(404, "not-found", f"{resource_type}/{resource_id} not found")

    @app.get("/fhir/{resource_type}")
    def search_type(resource_type: str, request: Request) -> JSONResponse:
        if resource_type not in RESOURCE_TABLES:
            return _problem(404, "not-supported",
                            f"unknown resource type {resource_type}")
        as_of, error = _guard(request, resource_type)
        if error is not None:
            return error

        params: Dict[str, Any] = dict(request.query_params)
        dates = request.query_params.getlist("date")
        if dates:
            params["date"] = dates
        try:
            patient_id, hosp_ids = search.resolve_context(store, params)
        except HttpProblem as exc:
            return _problem(exc.status, exc.code, exc.diagnostics)

        limit = int(params.get("_count", 1000))
        resources: List[Dict[str, Any]] = []
        for table in RESOURCE_TABLES[resource_type]:
            if table in _NOT_SEARCHABLE:
                continue
            preds = search.predicates_for(table, params)
            for hosp_id in hosp_ids:
                for row in store.query(table, hosp_id, as_of=as_of,
                                       predicates=preds, limit=limit):
                    resources.append(map_row(table, row, patient_id))
        return JSONResponse(media_type=FHIR_JSON, content=_bundle(resources[:limit]))

    return app
