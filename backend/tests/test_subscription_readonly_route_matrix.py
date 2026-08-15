# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Drive every gated route on the real app under read-only access.

``test_subscription_readonly_gate.py`` proves the gate's logic against
a mini app. This proves the classification against the *real* route
table: for each gated ``(route, method)``, a write is refused and a
read is not. The distinction matters because the mini app can only
test the rules, while the thing that actually goes wrong is a
particular route being on the wrong side of them.

Assertions are deliberately lopsided. A write must produce exactly
``SUBSCRIPTION_READONLY``; a read need only *not* produce it. Reads
run with the shared fixtures' in-memory repositories and a placeholder
in every path parameter, so most of them legitimately 404 or 422 —
what is being asserted is that the subscription gate let them through,
not that the handler was happy.
"""

from __future__ import annotations

import re
import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

import pytest
from app.auth import service as auth_service
from app.auth.route_access import AccessIntent, access_intent
from app.auth.service import require_active_subscription, require_baa_acceptance
from app.main import app
from app.route_introspection import iter_api_routes
from app.settings import get_settings
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from app.settings import Settings

SUBSCRIPTION_MODULE = f"{auth_service.__package__.rsplit('.', 1)[0]}.routes.subscription"

_PATH_PARAM = re.compile(r"\{[^}]+\}")


def _has_dependency(dependant: Any, target: Any) -> bool:
    if dependant.call is target:
        return True
    return any(_has_dependency(sub, target) for sub in dependant.dependencies)


def _gated_routes() -> list[tuple[str, str]]:
    """Every ``(method, path template)`` behind the subscription gate."""
    found: set[tuple[str, str]] = set()
    for path, route in iter_api_routes(app):
        if not _has_dependency(route.dependant, require_active_subscription):
            continue
        for method in route.methods or ():
            if method == "HEAD":
                continue
            found.add((method, path))
    return sorted(found)


GATED_ROUTES = _gated_routes()


def _concrete_url(path_template: str) -> str:
    """Fill path parameters with a placeholder the router will match."""
    return _PATH_PARAM.sub("placeholder", path_template)


def _error_code(response: Any) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    detail = body.get("detail", body) if isinstance(body, dict) else None
    if isinstance(detail, dict):
        code = detail.get("error", {}).get("code")
        return code if isinstance(code, str) else None
    return None


@pytest.fixture
def readonly_client(
    client: TestClient,  # installs the shared repository/auth overrides
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """The real app, with a read-only subscription and the gate live.

    ``conftest.py`` overrides both the gate and ``require_baa_acceptance``
    (which wraps it) so other tests never meet either. Drop just those
    two, keep every other override, and hand back a client that does not
    re-raise handler exceptions — a read that reaches its handler and
    fails there has already proven the point.
    """
    app.dependency_overrides.pop(require_active_subscription, None)
    app.dependency_overrides.pop(require_baa_acceptance, None)

    module = ModuleType(SUBSCRIPTION_MODULE)

    def fetch_subscription(_email: str, _settings: Settings) -> dict[str, str]:
        return {"status": "canceled", "access_level": "read_only"}

    module._fetch_subscription = fetch_subscription  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, SUBSCRIPTION_MODULE, module)

    settings = get_settings().model_copy(update={"pablo_edition": "solo"})
    monkeypatch.setattr(auth_service, "get_settings", lambda: settings)

    return TestClient(app, raise_server_exceptions=False)


def test_gated_route_set_is_not_empty() -> None:
    """Guard against the parametrization silently collapsing to nothing."""
    assert len(GATED_ROUTES) > 50, (
        f"Only {len(GATED_ROUTES)} gated routes found — route introspection "
        "has likely gone blind, which would make the matrix below pass "
        "vacuously."
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [pytest.param(m, p, id=f"{m}-{p}") for m, p in GATED_ROUTES],
)
def test_read_only_subscription_blocks_writes_and_allows_reads(
    readonly_client: TestClient, method: str, path: str
) -> None:
    response = readonly_client.request(method, _concrete_url(path))
    code = _error_code(response)

    if access_intent(method, path) is AccessIntent.WRITE:
        assert response.status_code == 403, (
            f"{method} {path} is write-intent but was not refused under a "
            f"read-only subscription (got {response.status_code})."
        )
        assert code == "SUBSCRIPTION_READONLY", (
            f"{method} {path} was refused with {code!r} rather than SUBSCRIPTION_READONLY."
        )
    else:
        assert code != "SUBSCRIPTION_READONLY", (
            f"{method} {path} is read-intent but the subscription gate "
            "refused it. Either the handler writes — in which case add an "
            "AccessIntent.WRITE override in app.auth.route_access — or the "
            "classification is wrong."
        )
