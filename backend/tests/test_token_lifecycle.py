# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Every route behind the bearer-token verifier must reject a bad token
in the auth layer, before any handler runs.

A session can go bad in several different ways after login: the ID token
expires, gets revoked, the account is disabled, the account is deleted, or
the identity provider itself stops answering. Each of those is exercised
once at the unit level in ``test_auth.py``. This suite treats them as one
class and checks the fan-out: for *every* route that authenticates through
``require_mfa`` / ``get_current_user_no_mfa`` (the shared bearer-token
verifier boundary), each bad-token state must come back as the intended
4xx/503 with a stable error code — never a 500, and never far enough to
touch a repository.

Routes are enumerated from the live app at collection time (see
``app.route_introspection``), the same approach ``test_route_mfa_guardrails``
uses, so a new route is covered automatically without a manual list to
maintain. A route is "public" if ``truly_public`` is anywhere in its
dependency tree; everything else that reaches ``require_mfa`` or
``get_current_user_no_mfa`` is "authenticated" for this suite.

Two other route families sit behind a *different* verifier and are not
covered here: service-account routes (``require_pentest_runner`` /
``require_cloud_tasks_invoker``) check a Google-signed OIDC token, not a
Firebase ID token, so the states below don't mean anything to them; and
patient-facing routes (``get_patient_context``) resolve through a
separate patient-credential registry that never calls the Firebase
verifier at all. Both already reject un-owned or bad credentials on their
own terms — just not via the token states this suite fakes.

The Firebase verifier is faked at the same seam ``test_auth.py`` uses
(``app.auth.service.firebase_auth.verify_id_token``), never via
``app.dependency_overrides`` — this suite exists specifically to exercise
the real dependency chain, including the middleware's cached-token path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from app.auth.route_security import truly_public
from app.auth.service import get_current_user_no_mfa, get_session_peek_claims, require_mfa
from app.main import app
from app.route_introspection import iter_api_routes
from fastapi.testclient import TestClient
from firebase_admin import auth as firebase_auth
from firebase_admin import exceptions as firebase_exceptions

from tests.conftest import _mock_session_instance

if TYPE_CHECKING:
    from collections.abc import Iterator

VERIFY_PATCH = "app.auth.service.firebase_auth.verify_id_token"

# Markers that put a route behind the Firebase/OIDC bearer verifier this
# suite fakes. Both ultimately call ``_verify_request_identity``.
_AUTHENTICATED_MARKERS: tuple[Any, ...] = (
    require_mfa,
    get_current_user_no_mfa,
    get_session_peek_claims,
)
_PUBLIC_MARKERS: tuple[Any, ...] = (truly_public,)

_PLACEHOLDER_PATH_PARAM = "00000000-0000-0000-0000-000000000000"
_PATH_PARAM_RE = re.compile(r"\{[^}]+\}")


def _has_dependency(dependant: Any, target_callables: tuple[Any, ...]) -> bool:
    """True if any of ``target_callables`` appears anywhere in this tree.

    Mirrors ``test_route_mfa_guardrails._has_dependency`` — same traversal,
    so the two suites agree on what "reaches this marker" means.
    """
    if dependant.call in target_callables:
        return True
    return any(_has_dependency(sub, target_callables) for sub in dependant.dependencies)


def _iter_authenticated_routes() -> Iterator[tuple[str, str]]:
    """Yield (path, method) for every authenticated API route.

    ``iter_api_routes`` only yields ``APIRoute`` instances, so framework
    routes like ``/docs`` and ``/openapi.json`` (mounted as plain Starlette
    routes) are already excluded — no explicit allowlist needed for them.
    """
    for path, route in iter_api_routes(app):
        if _has_dependency(route.dependant, _PUBLIC_MARKERS):
            continue
        if not _has_dependency(route.dependant, _AUTHENTICATED_MARKERS):
            continue
        for method in route.methods or ():
            if method == "HEAD":
                continue
            yield path, method


AUTHENTICATED_ROUTES: list[tuple[str, str]] = list(_iter_authenticated_routes())


@dataclass(frozen=True)
class TokenState:
    """One row of the bad-token table: how to fake it, and what a route
    behind the verifier must answer with."""

    name: str
    exception: Exception | None
    expected_status: int
    expected_code: str | None
    send_bearer: bool = True


# The one table constant every case in this module is checked against.
# Mirrors the states already unit-tested in ``test_auth.py``.
TOKEN_STATES: tuple[Any, ...] = (
    pytest.param(
        TokenState(
            name="missing_bearer",
            exception=None,
            expected_status=401,
            expected_code=None,
            send_bearer=False,
        ),
        marks=pytest.mark.xfail(
            reason=(
                "No Authorization header never reaches app code — FastAPI's "
                "HTTPBearer security dependency rejects it with a bare "
                '{"detail": "Not authenticated"} string, not the app-wide '
                '{"error": {"code": ...}} envelope every other rejection in '
                "this table uses. Real gap, not an auth behavior change: see "
                "api_errors.NO_CREDENTIALS, which already documents the same "
                "shape mismatch for the auth-failure-spike log line."
            ),
            strict=True,
        ),
        id="missing_bearer",
    ),
    pytest.param(
        TokenState(
            name="malformed_token",
            exception=firebase_auth.InvalidIdTokenError("Bad token"),
            expected_status=401,
            expected_code="INVALID_TOKEN",
        ),
        id="malformed_token",
    ),
    pytest.param(
        TokenState(
            name="expired",
            exception=firebase_auth.ExpiredIdTokenError("Token expired", cause=None),
            expected_status=401,
            expected_code="TOKEN_EXPIRED",
        ),
        id="expired",
    ),
    pytest.param(
        TokenState(
            name="revoked",
            exception=firebase_auth.RevokedIdTokenError("Token revoked"),
            expected_status=401,
            expected_code="TOKEN_REVOKED",
        ),
        id="revoked",
    ),
    pytest.param(
        TokenState(
            name="deleted_user",
            exception=firebase_auth.UserNotFoundError("No user record found"),
            expected_status=401,
            expected_code="USER_NOT_FOUND",
        ),
        id="deleted_user",
    ),
    pytest.param(
        TokenState(
            name="disabled_user",
            exception=firebase_auth.UserDisabledError("User disabled"),
            expected_status=401,
            expected_code="USER_DISABLED",
        ),
        id="disabled_user",
    ),
    pytest.param(
        TokenState(
            name="provider_timeout",
            exception=firebase_exceptions.DeadlineExceededError(
                "Timed out while making an API call", cause=None
            ),
            expected_status=503,
            expected_code="AUTH_PROVIDER_UNAVAILABLE",
        ),
        id="provider_timeout",
    ),
)


ROUTE_IDS: list[str] = [f"{method} {path}" for path, method in AUTHENTICATED_ROUTES]


@pytest.fixture(scope="module")
def raw_client() -> TestClient:
    """A TestClient with no dependency overrides — the real auth chain runs."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_dependency_overrides() -> Iterator[None]:
    """Guarantee no other test's ``dependency_overrides`` leak into this
    module — the whole point of this suite is exercising the real chain."""
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_mock_session() -> Iterator[None]:
    """Reset the shared mocked session, and give it empty change-tracking
    collections for the duration of this test.

    ``DatabaseSessionMiddleware`` unconditionally checks
    ``session.dirty or session.new or session.deleted`` after every request
    to guard against committing to the wrong tenant schema. A bare
    ``MagicMock`` answers all three with a (truthy) auto-generated attribute,
    so that guard — and the query it issues — fires on every request
    regardless of whether a handler ever ran. That is a property of the
    shared mock, not of auth rejection, so it would otherwise mask the
    thing this suite checks: that a rejected request never reaches a
    repository. Restored afterward since ``_mock_session_instance`` is a
    module-level singleton shared with every other test file.
    """
    _mock_session_instance.reset_mock()
    original = (
        _mock_session_instance.dirty,
        _mock_session_instance.new,
        _mock_session_instance.deleted,
    )
    _mock_session_instance.dirty = set()
    _mock_session_instance.new = set()
    _mock_session_instance.deleted = set()
    try:
        yield
    finally:
        _mock_session_instance.dirty, _mock_session_instance.new, _mock_session_instance.deleted = (
            original
        )


@pytest.mark.parametrize(("path", "method"), AUTHENTICATED_ROUTES, ids=ROUTE_IDS)
@pytest.mark.parametrize("state", TOKEN_STATES)
def test_bad_token_state_rejected_before_handler(
    raw_client: TestClient,
    state: TokenState,
    path: str,
    method: str,
) -> None:
    concrete_path = _PATH_PARAM_RE.sub(_PLACEHOLDER_PATH_PARAM, path)
    headers = {"Authorization": "Bearer test-token"} if state.send_bearer else {}

    if state.exception is not None:
        with patch(VERIFY_PATCH, side_effect=state.exception):
            response = raw_client.request(method, concrete_path, headers=headers)
    else:
        response = raw_client.request(method, concrete_path, headers=headers)

    assert response.status_code == state.expected_status, response.text
    assert response.status_code != 500

    detail = response.json().get("detail")
    assert isinstance(detail, dict), (
        f"expected a stable {{'error': {{'code': ...}}}} envelope, got {detail!r}"
    )
    assert detail.get("error", {}).get("code") == state.expected_code

    # The handler never ran: the only SQL the mocked session saw is the
    # middleware's own unconditional "SET search_path" (issued for every
    # request, success or failure, before any dependency runs — see
    # ``DatabaseSessionMiddleware._prepare``). A repository query beyond
    # that one statement would mean a handler (or a dependency downstream
    # of the auth check) got far enough to touch the database.
    assert _mock_session_instance.execute.call_count == 1


def test_finds_at_least_25_authenticated_routes() -> None:
    """Tripwire: if route enumeration ever goes blind (see the identical
    concern in ``test_route_mfa_guardrails.test_route_enumeration_is_not_blind``),
    this suite would otherwise silently cover nothing and still report green."""
    distinct_routes = set(AUTHENTICATED_ROUTES)
    assert len(distinct_routes) >= 25, (
        f"Only {len(distinct_routes)} authenticated routes enumerated — "
        "expected 25+. Route introspection or the marker classification "
        "above has likely broken."
    )
