# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""HTTP tests for the public practice directory.

Mounts the real router on a fresh FastAPI app, with
``create_standalone_session`` patched to a small in-memory stand-in for the two
platform tables involved. What is under test is the real handler logic: the
uniform 404, the idempotent get-or-create, the collision suffix and the
reserved-word refusal.

What these tests are FOR, in order of how much they would hurt to get wrong:

* **Unknown and disabled slugs are the same 404** — status AND body — or the
  directory becomes an oracle for which practices exist.
* **The response never carries an internal identifier.**
* **Idempotency** — a clinician re-asking for their link must not mint a second
  address for the same practice.
* **A name collision resolves to a suffixed slug**, and a reserved word is
  never handed out bare.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast
from unittest.mock import patch

import pytest
from app.auth.service import require_active_subscription
from app.db.platform_models import PortalPracticeSlugRow, PracticeRow
from app.portal.factory import PORTAL_REDEEM_PATH
from app.portal.practice_routes import _RESERVED_SLUGS, router
from app.rate_limit import (
    require_portal_practice_resolve_rate_limit,
    reset_portal_limiters,
)
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models import User
    from fastapi import Request

RESOLVE_URL = "/api/portal/practices/{slug}"
MINT_URL = "/api/portal/practice-slug"

PRACTICE_ID = "practice-1"


class _FakeOrigError(Exception):
    def __init__(self, pgcode: str) -> None:
        super().__init__(pgcode)
        self.pgcode = pgcode


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row


class _FakeNested:
    """Just enough of ``Session.begin_nested()`` for the savepoint pattern: a
    context manager whose body may raise, which the caller catches."""

    def __enter__(self) -> _FakeNested:
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        return False


class _FakeSession:
    """An in-memory stand-in for the two platform tables these routes touch.

    ``slugs`` is a plain dict keyed by slug — a second ``add`` plus ``flush``
    for an already-present key raises ``IntegrityError`` with a 23505
    ``orig.pgcode``, mirroring the real unique-constraint collision.
    """

    def __init__(
        self,
        practices: dict[str, PracticeRow] | None = None,
        slugs: dict[str, PortalPracticeSlugRow] | None = None,
    ) -> None:
        self.practices = practices or {}
        self.slugs = slugs or {}
        self._pending: PortalPracticeSlugRow | None = None
        self.committed = 0
        self.closed = False

    def get(self, model: type, key: Any) -> Any:
        if model is PracticeRow:
            return self.practices.get(key)
        if model is PortalPracticeSlugRow:
            return self.slugs.get(key)
        raise AssertionError(f"unexpected model {model!r}")

    def execute(self, stmt: Any) -> _FakeResult:
        # The only statement these routes issue: select(PortalPracticeSlugRow)
        # .where(PortalPracticeSlugRow.practice_id == <bound value>).
        params = stmt.compile().params
        (practice_id,) = params.values()
        row = next((r for r in self.slugs.values() if r.practice_id == practice_id), None)
        return _FakeResult(row)

    def add(self, row: PortalPracticeSlugRow) -> None:
        self._pending = row

    def flush(self) -> None:
        assert self._pending is not None
        if self._pending.slug in self.slugs:
            raise IntegrityError("INSERT", {}, _FakeOrigError("23505"))
        self.slugs[self._pending.slug] = self._pending

    def begin_nested(self) -> _FakeNested:
        return _FakeNested()

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self._pending = None

    def close(self) -> None:
        self.closed = True


def _practice(practice_id: str = PRACTICE_ID, name: str = "Example Therapy") -> PracticeRow:
    return PracticeRow(
        id=practice_id,
        name=name,
        schema_name=f"practice_{practice_id}",
        owner_email="owner@example.test",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _slug_row(
    slug: str,
    *,
    practice_id: str = PRACTICE_ID,
    display_name: str = "Example Therapy",
    enabled: bool = True,
) -> PortalPracticeSlugRow:
    return PortalPracticeSlugRow(
        slug=slug,
        practice_id=practice_id,
        display_name=display_name,
        enabled=enabled,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture(autouse=True)
def _clean_rate_limits() -> Iterator[None]:
    reset_portal_limiters()
    yield
    reset_portal_limiters()


@pytest.fixture
def fake_db() -> _FakeSession:
    return _FakeSession(practices={PRACTICE_ID: _practice()})


@pytest.fixture
def app(mock_user: User) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[require_active_subscription] = lambda: mock_user
    return application


@pytest.fixture
def client(app: FastAPI, fake_db: _FakeSession) -> Iterator[TestClient]:
    with (
        patch("app.portal.practice_routes.create_standalone_session", lambda: fake_db),
        patch(
            "app.portal.practice_routes._resolve_practice_from_email",
            lambda _email: (PRACTICE_ID, f"practice_{PRACTICE_ID}"),
        ),
    ):
        yield TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/portal/practices/{slug} — the public resolve
# ---------------------------------------------------------------------------


def test_resolve_returns_slug_and_display_name_only(
    client: TestClient, fake_db: _FakeSession
) -> None:
    fake_db.slugs["example-therapy"] = _slug_row("example-therapy")

    response = client.get(RESOLVE_URL.format(slug="example-therapy"))

    assert response.status_code == 200
    assert response.json() == {"slug": "example-therapy", "display_name": "Example Therapy"}
    raw = response.text
    assert PRACTICE_ID not in raw
    assert "schema_name" not in raw
    assert f"practice_{PRACTICE_ID}" not in raw


def test_resolve_does_not_say_whether_a_practice_turned_the_portal_off(
    client: TestClient, fake_db: _FakeSession
) -> None:
    """Unknown and disabled answer identically, status AND body. A response
    that distinguished them — an ``enabled`` field, or a different status —
    would be exactly the oracle this 404 exists to withhold."""
    fake_db.slugs["turned-off"] = _slug_row("turned-off", enabled=False)

    unknown = client.get(RESOLVE_URL.format(slug="never-existed"))
    disabled = client.get(RESOLVE_URL.format(slug="turned-off"))

    assert unknown.status_code == disabled.status_code == 404
    assert unknown.json() == disabled.json()
    assert "enabled" not in disabled.text


def test_resolve_engages_the_per_address_rate_limit() -> None:
    """Called directly: the route-level tests above leave the real dependency
    in place, but proving the window closes needs 31 calls, so the limiter's
    own behaviour is exercised in isolation."""

    class _FakeRequest:
        def __init__(self, ip: str) -> None:
            self.headers: dict[str, str] = {}
            self.client = type("Client", (), {"host": ip})()

    request = cast("Request", _FakeRequest("203.0.113.9"))
    for _ in range(30):
        require_portal_practice_resolve_rate_limit(request)

    with pytest.raises(HTTPException) as exc_info:
        require_portal_practice_resolve_rate_limit(request)
    assert exc_info.value.status_code == 429


# ---------------------------------------------------------------------------
# POST /api/portal/practice-slug — the clinician mint
# ---------------------------------------------------------------------------


def test_mint_refuses_an_unauthenticated_caller(fake_db: _FakeSession) -> None:
    """No auth override — the real dependency runs and refuses."""
    application = FastAPI()
    application.include_router(router)

    with patch("app.portal.practice_routes.create_standalone_session", lambda: fake_db):
        response = TestClient(application).post(MINT_URL)

    assert response.status_code in {401, 403}


def test_mint_is_idempotent(client: TestClient, fake_db: _FakeSession) -> None:
    first = client.post(MINT_URL)
    second = client.post(MINT_URL)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len([r for r in fake_db.slugs.values() if r.practice_id == PRACTICE_ID]) == 1


def test_mint_slugifies_the_practice_name(client: TestClient) -> None:
    response = client.post(MINT_URL)

    assert response.status_code == 200
    assert response.json()["slug"] == "example-therapy"


def test_mint_resolves_a_name_collision_with_a_numeric_suffix(
    client: TestClient, fake_db: _FakeSession
) -> None:
    fake_db.slugs["example-therapy"] = _slug_row(
        "example-therapy", practice_id="some-other-practice"
    )

    response = client.post(MINT_URL)

    assert response.status_code == 200
    assert response.json()["slug"] == "example-therapy-2"


def test_mint_never_hands_out_a_reserved_word_bare(fake_db: _FakeSession, mock_user: User) -> None:
    """``redeem`` is the load-bearing one: it is the segment a magic link lands
    on, so a practice that took it would shadow every invitation."""
    fake_db.practices[PRACTICE_ID] = _practice(name="Redeem")
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[require_active_subscription] = lambda: mock_user

    with (
        patch("app.portal.practice_routes.create_standalone_session", lambda: fake_db),
        patch(
            "app.portal.practice_routes._resolve_practice_from_email",
            lambda _email: (PRACTICE_ID, f"practice_{PRACTICE_ID}"),
        ),
    ):
        response = TestClient(application).post(MINT_URL)

    assert response.status_code == 200
    assert response.json()["slug"] != "redeem"
    assert response.json()["slug"] == "redeem-2"


def test_the_magic_links_landing_segment_is_reserved() -> None:
    """The two halves have to agree, and nothing else makes them.

    ``app.portal.factory`` mints every invitation at ``/portal/redeem``, and
    the shell's practice pages sit beside it at ``/portal/{slug}``. A practice
    that could take ``redeem`` as its address would shadow the one path a
    patient has to arrive on.
    """
    landing_segment = PORTAL_REDEEM_PATH.rsplit("/", 1)[-1]
    assert landing_segment in _RESERVED_SLUGS
