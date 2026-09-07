# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``GET /openapi.json`` builds.

Every other test in the suite calls the routes, and the routes work whether
or not the framework managed to resolve their annotations: a dependency it
failed to recognise is read as a query parameter instead, which nothing
notices until something asks for the document that has to describe it. Then
the spec is a 500 for every consumer of it — a generated client, an API
console, contract tooling, a scanner enumerating routes. This is the ask
that was missing.
"""

from __future__ import annotations

from app.main import app
from fastapi.testclient import TestClient

_JOB_PATH = "/api/internal/jobs/check-eligibility"


def test_openapi_document_builds() -> None:
    response = TestClient(app).get("/openapi.json")

    assert response.status_code == 200


def test_openapi_document_describes_the_coverage_routes() -> None:
    paths = TestClient(app).get("/openapi.json").json()["paths"]

    assert "/api/patients/{patient_id}/coverage" in paths
    assert "/api/payers" in paths
    assert _JOB_PATH in paths


def test_injected_dependencies_are_not_query_parameters() -> None:
    """The repositories the eligibility worker injects are not part of its API.

    They were, once: an unresolvable annotation elsewhere in the signature
    left the framework unable to read the ``Annotated[..., Depends(...)]``
    aliases beside it, so three repositories were published as required
    query parameters — and the document could not describe them.
    """
    operation = TestClient(app).get("/openapi.json").json()["paths"][_JOB_PATH]["post"]

    assert [parameter["name"] for parameter in operation.get("parameters", [])] == []
