# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Practice invitations, end to end against a real provisioned practice.

The owner invites ``a@…``; an account signing in as ``b@…`` redeems the link,
confirming the different address. Then, with the practice-resolution cache on:

* b's next signed-in request resolves to the owner's practice, through the
  schema middleware and ``get_tenant_context``, as served;
* b is listed in ``GET /api/practice/admins``;
* the redeem is audited in the practice, with ids only, and the membership
  hook is called once.

And every refusal writes nothing: a link used twice, expired or withdrawn
(410), a different address without confirmation (409 ``EMAIL_MISMATCH``), and
an account or address that already belongs to a practice (409
``ALREADY_IN_PRACTICE``).

Nothing on the redeem path is stubbed except the identity provider (the
sign-in token is taken as verified for the address the test names) and Redis
(the in-memory stand-in the cache's own suite uses).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from alembic import command
from alembic.config import Config
from app.api_errors import register_exception_handlers
from app.auth.providers import VerifiedIdentity
from app.auth.service import get_current_user, require_active_subscription
from app.db import arm_current_user_id, clear_tenant_schema, practice_members, set_tenant_schema
from app.db.provisioning import create_practice_schema
from app.models import User
from app.models.audit import AuditAction
from app.routes import practice_admins as practice_admin_routes
from app.routes import practice_invites as invite_routes
from app.services.email_sender import InMemoryEmailSender
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from tests.practice_resolution_fakes import FakeRedis

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_SUFFIX = uuid.uuid4().hex[:8]
_SCHEMA = f"practice_test_invites_{_SUFFIX}"
_PRACTICE_ID = f"invites-test-{_SUFFIX}"
_PRACTICE_NAME = "Harbor Counseling"
_OTHER_PRACTICE_ID = f"invites-other-{_SUFFIX}"
_OWNER_ID = str(uuid.uuid4())
_OWNER_EMAIL = f"owner-{_SUFFIX}@example.test"

OWNER = User(
    id=_OWNER_ID,
    email=_OWNER_EMAIL,
    name="Olive Owner",
    created_at=datetime.now(UTC),
    baa_accepted_at=datetime.now(UTC),
    baa_version="2024-01-01",
)


def _address(name: str) -> str:
    return f"{name}-{_SUFFIX}-{uuid.uuid4().hex[:6]}@example.test"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    """The real database, migrated, with this module's practice and its owner."""
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    create_practice_schema(eng, _SCHEMA)
    with eng.begin() as conn:
        for practice_id, schema in (
            (_PRACTICE_ID, _SCHEMA),
            (_OTHER_PRACTICE_ID, f"{_SCHEMA}_other"),
        ):
            conn.execute(
                text(
                    "INSERT INTO platform.practices (id, name, schema_name, owner_email,"
                    " product, status, is_active, created_at)"
                    " VALUES (:i, :n, :s, :e, 'pablo', 'active', true, now())"
                ),
                {"i": practice_id, "n": _PRACTICE_NAME, "s": schema, "e": _OWNER_EMAIL},
            )
        conn.execute(
            text(
                "INSERT INTO platform.users (id, email, name, created_at, status,"
                " is_platform_admin, chat_quality_review_opt_in,"
                " session_notes_quality_review_opt_in, inbox_quality_review_opt_in)"
                " VALUES (CAST(:i AS uuid), :e, 'Olive Owner', now(), 'approved', false,"
                " false, false, false)"
            ),
            {"i": _OWNER_ID, "e": _OWNER_EMAIL},
        )
        conn.execute(
            text("UPDATE platform.practices SET owner_user_id = CAST(:o AS uuid) WHERE id = :p"),
            {"o": _OWNER_ID, "p": _PRACTICE_ID},
        )
        conn.execute(
            text(
                "INSERT INTO platform.email_tenant_mappings"
                " (email, tenant_id, practice_id, created_at) VALUES (:e, :p, :p, now())"
            ),
            {"e": _OWNER_EMAIL, "p": _PRACTICE_ID},
        )
    yield eng
    with eng.begin() as conn:
        practices = {"ids": [_PRACTICE_ID, _OTHER_PRACTICE_ID]}
        users = conn.execute(
            text(
                "SELECT user_id FROM platform.practice_members WHERE practice_id = ANY(:ids)"
                " UNION SELECT CAST(:o AS uuid)"
            ),
            {**practices, "o": _OWNER_ID},
        ).scalars()
        user_ids = [str(u) for u in users]
        conn.execute(
            text("DELETE FROM platform.allowed_emails WHERE practice_id = ANY(:ids)"), practices
        )
        conn.execute(
            text("DELETE FROM platform.email_tenant_mappings WHERE practice_id = ANY(:ids)"),
            practices,
        )
        conn.execute(
            text("DELETE FROM platform.companion_practice_slugs WHERE practice_id = ANY(:ids)"),
            practices,
        )
        conn.execute(text("DELETE FROM platform.practices WHERE id = ANY(:ids)"), practices)
        conn.execute(
            text("DELETE FROM platform.user_identities WHERE user_id = ANY(CAST(:u AS uuid[]))"),
            {"u": user_ids},
        )
        conn.execute(
            text("DELETE FROM platform.users WHERE id = ANY(CAST(:u AS uuid[]))"), {"u": user_ids}
        )
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE'))
    eng.dispose()


class _Harness:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.session = Session(engine)
        self.sender = InMemoryEmailSender()
        self.redis = FakeRedis()
        self.hook_calls: list[tuple[str, str, str]] = []
        self.signed_in: dict[str, VerifiedIdentity] = {}

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(invite_routes.router)
        app.include_router(invite_routes.invitee_router)
        app.include_router(practice_admin_routes.router)
        app.dependency_overrides[get_current_user] = lambda: OWNER
        app.dependency_overrides[require_active_subscription] = lambda: OWNER
        app.dependency_overrides[invite_routes.get_invite_email_sender] = lambda: self.sender
        self.client = TestClient(app, raise_server_exceptions=False)

    # --- the owner's side ---

    def as_owner(self) -> TestClient:
        set_tenant_schema(self.session, _SCHEMA)
        arm_current_user_id(self.session, _OWNER_ID)
        return self.client

    def invite(self, email: str) -> tuple[str, str]:
        """The owner invites ``email``: the invitation's id, and the emailed token."""
        response = self.as_owner().post("/api/practice/invites", json={"email": email})
        assert response.status_code == 201, response.text
        token = self.sender.sent[-1].text.split("/invite#", 1)[1].split()[0]
        return str(response.json()["id"]), token

    # --- the invitee's side ---

    def sign_in(self, email: str) -> VerifiedIdentity:
        """A sign-in token the provider verified for ``email``; no account yet."""
        subject_id = f"sub-{uuid.uuid4().hex[:12]}"
        identity = VerifiedIdentity(
            provider="firebase",
            subject_id=subject_id,
            email=email,
            email_verified=True,
            mfa_satisfied=False,
            claims={"uid": subject_id, "email": email, "email_verified": True, "name": "Bo"},
        )
        self.signed_in["current"] = identity
        return identity

    def preview(self, token: str) -> Any:
        """``POST /api/invites/preview``, signed out: the token in the body."""
        return self.client.post("/api/invites/preview", json={"token": token})

    def redeem(self, token: str, *, confirm_mismatch: bool = False) -> Any:
        """``POST /api/invites/redeem`` as the current sign-in, on a request with no practice."""
        clear_tenant_schema(self.session)
        return self.client.post(
            "/api/invites/redeem",
            json={"token": token, "confirm_mismatch": confirm_mismatch},
            headers={"Authorization": "Bearer stand-in"},
        )

    def next_request(self, identity: VerifiedIdentity) -> tuple[str | None, Any]:
        """One signed-in request through the middleware and ``get_tenant_context``."""
        from app.auth.service import get_tenant_context  # noqa: PLC0415
        from app.db.middleware import _resolve_schema_from_request  # noqa: PLC0415

        request = SimpleNamespace(state=SimpleNamespace(verified_identity=identity))
        schema, _reason = _resolve_schema_from_request(request)  # type: ignore[arg-type]
        user_repo = MagicMock()
        user_repo.get.return_value = None
        with (
            patch("app.db.get_db_session", return_value=MagicMock()),
            patch("app.db.arm_current_user_id"),
        ):
            try:
                context = get_tenant_context(request, identity.claims, user_repo, MagicMock())  # type: ignore[arg-type]
            except HTTPException as exc:
                return schema, exc.status_code
        return schema, context.practice_id

    # --- what is in the database ---

    def scalar(self, sql: str, **params: Any) -> Any:
        with self.engine.connect() as conn:
            return conn.execute(text(sql), params).scalar()

    def account_id(self, identity: VerifiedIdentity) -> str | None:
        found = self.scalar(
            "SELECT user_id FROM platform.user_identities WHERE subject_id = :s",
            s=identity.subject_id,
        )
        return None if found is None else str(found)

    def footprint(self) -> dict[str, int]:
        """Row counts every redeem would change."""
        counts = {
            table: int(self.scalar(f"SELECT count(*) FROM platform.{table}"))  # noqa: S608 — fixed table names
            for table in (
                "users",
                "user_identities",
                "practice_members",
                "email_tenant_mappings",
                "allowed_emails",
            )
        }
        counts["redeemed"] = int(
            self.scalar(
                "SELECT count(*) FROM platform.practice_invites WHERE redeemed_at IS NOT NULL"
            )
        )
        counts["audit"] = len(self._audit("SELECT action, user_id, changes"))
        return counts

    def _audit(self, select: str) -> list[Any]:
        """Every audit row in the practice, whoever wrote it.

        ``audit_logs`` is FORCE row-level security, each row visible to its own
        actor; the purge GUC is the one setting that reads them all. Armed
        for this read alone, in a transaction that is rolled back.
        """
        with self.engine.connect() as conn:
            conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
            rows = conn.execute(
                text(f'{select} FROM "{_SCHEMA}".audit_logs ORDER BY timestamp')
            ).all()
            conn.rollback()
        return list(rows)

    def audit_rows(self) -> list[tuple[str, str, dict[str, Any]]]:
        rows = self._audit("SELECT action, user_id, changes")
        return [
            (str(a), str(u), dict(c or {}))
            for a, u, c in rows
            if str(a).startswith("practice_invite_")
        ]


@pytest.fixture
def harness(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Harness]:
    made = _Harness(engine)
    monkeypatch.setattr(practice_members, "_membership_hooks", [])
    practice_members.register_membership_hook(lambda p, u, c: made.hook_calls.append((p, u, c)))
    with (
        patch("app.db.get_db_session", return_value=made.session),
        patch.object(invite_routes, "get_db_session", return_value=made.session),
        patch.object(practice_admin_routes, "get_db_session", return_value=made.session),
        patch(
            "app.auth.invite_identity._verify_request_identity",
            side_effect=lambda _request, _token: made.signed_in["current"],
        ),
        patch("app.auth.idle_session.check_and_touch"),
        patch("app.db.practice_resolution_cache.get_redis_client", return_value=made.redis),
    ):
        try:
            yield made
        finally:
            made.session.rollback()
            made.session.close()


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


# --- AC1: the invitation binds to whichever account redeems it ---------------


def test_an_account_with_another_address_redeems_and_is_in_the_practice(
    harness: _Harness,
) -> None:
    invited = _address("a")
    _invite_id, token = harness.invite(invited)
    preview = harness.preview(token)
    assert preview.status_code == 200
    assert preview.json() == {"practice_name": _PRACTICE_NAME, "email": invited}

    bee = harness.sign_in(_address("b"))
    assert harness.next_request(bee) == (None, 403), "no practice before the redeem"

    response = harness.redeem(token, confirm_mismatch=True)
    assert response.status_code == 200, response.text
    assert response.json() == {"practice_id": _PRACTICE_ID, "practice_name": _PRACTICE_NAME}

    # The next request resolves to the owner's practice, with Redis on, and the
    # one after it is answered from the cache.
    assert harness.next_request(bee) == (_SCHEMA, _PRACTICE_ID)
    account = harness.account_id(bee)
    assert account is not None
    assert harness.redis.get(f"practice-resolution:v1:user:{account}") is not None
    assert harness.next_request(bee) == (_SCHEMA, _PRACTICE_ID)

    membership = harness.scalar(
        "SELECT joined_via || '/' || coalesce(invited_by::text, '') || '/' ||"
        " coalesce(claimed_email, '') FROM platform.practice_members"
        " WHERE user_id = CAST(:u AS uuid) AND practice_id = :p",
        u=account,
        p=_PRACTICE_ID,
    )
    assert membership == f"invite/{_OWNER_ID}/{bee.email}", "records the granted address"

    listed = harness.as_owner().get("/api/practice/admins")
    assert listed.status_code == 200, listed.text
    assert bee.email in [c["email"] for c in listed.json()["clinicians"]]

    assert harness.hook_calls == [(_PRACTICE_ID, account, "joined")]
    assert harness.preview(token).status_code == 410


def test_revoking_the_granted_address_takes_the_invited_member_out(harness: _Harness) -> None:
    """``DELETE /api/admin/allowlist/{email}`` is how an operator removes a
    clinician; for an invited member it has to remove the membership too."""
    from app.repositories.postgres.allowlist import PostgresAllowlistRepository  # noqa: PLC0415

    _id, token = harness.invite(_address("revoked-invited"))
    bee = harness.sign_in(_address("revoked-redeemer"))
    assert harness.redeem(token, confirm_mismatch=True).status_code == 200
    assert harness.next_request(bee) == (_SCHEMA, _PRACTICE_ID), "and now cached"

    clear_tenant_schema(harness.session)
    assert PostgresAllowlistRepository(harness.session).remove(bee.email) is True
    harness.session.commit()

    account = harness.account_id(bee)
    assert harness.redis.get(f"practice-resolution:v1:user:{account}") is None
    assert harness.next_request(bee) == (None, 403)
    for table in ("practice_members", "email_tenant_mappings", "allowed_emails"):
        column = "user_id::text" if table == "practice_members" else "email"
        value = account if table == "practice_members" else bee.email
        found = harness.scalar(
            f"SELECT count(*) FROM platform.{table} WHERE {column} = :v",  # noqa: S608 — fixed names
            v=value,
        )
        assert found == 0, table


# --- AC2: reuse, expiry and revocation ----------------------------------------


def test_a_link_redeemed_once_cannot_be_redeemed_again(harness: _Harness) -> None:
    invited = _address("once")
    _id, token = harness.invite(invited)
    harness.sign_in(invited)
    assert harness.redeem(token).status_code == 200
    hooks = list(harness.hook_calls)

    harness.sign_in(_address("second"))
    before = harness.footprint()
    response = harness.redeem(token, confirm_mismatch=True)
    assert response.status_code == 410
    assert _code(response) == "INVITE_UNAVAILABLE"
    assert harness.footprint() == before
    assert harness.hook_calls == hooks


def test_an_expired_link_is_gone_and_writes_nothing(harness: _Harness) -> None:
    invited = _address("late")
    invite_id, token = harness.invite(invited)
    with harness.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE platform.practice_invites SET expires_at = now() - interval '1 second'"
                " WHERE id = CAST(:i AS uuid)"
            ),
            {"i": invite_id},
        )
    assert harness.preview(token).status_code == 410
    harness.sign_in(invited)
    before = harness.footprint()
    assert harness.redeem(token).status_code == 410
    assert harness.footprint() == before
    assert harness.hook_calls == []
    pending = harness.as_owner().get("/api/practice/invites").json()["invites"]
    assert invite_id not in [i["id"] for i in pending]


def test_a_withdrawn_link_is_gone_and_writes_nothing(harness: _Harness) -> None:
    invited = _address("withdrawn")
    invite_id, token = harness.invite(invited)
    pending = harness.as_owner().get("/api/practice/invites").json()["invites"]
    assert invite_id in [i["id"] for i in pending]

    withdrawn = harness.as_owner().delete(f"/api/practice/invites/{invite_id}")
    assert withdrawn.status_code == 204
    pending = harness.as_owner().get("/api/practice/invites").json()["invites"]
    assert invite_id not in [i["id"] for i in pending]

    harness.sign_in(invited)
    before = harness.footprint()
    assert harness.redeem(token).status_code == 410
    assert harness.footprint() == before
    assert harness.hook_calls == []


def test_a_link_into_an_inactive_practice_is_gone_and_writes_nothing(
    harness: _Harness,
) -> None:
    """Offboarding sets ``is_active`` false, with ``deleted_at``: a deleted
    practice is refused by the same check."""
    invited = _address("inactive")
    _id, token = harness.invite(invited)
    harness.sign_in(invited)
    with harness.engine.begin() as conn:
        conn.execute(
            text("UPDATE platform.practices SET is_active = FALSE WHERE id = :p"),
            {"p": _PRACTICE_ID},
        )
    try:
        before = harness.footprint()
        assert harness.preview(token).status_code == 410
        assert harness.redeem(token).status_code == 410
        assert harness.footprint() == before
        assert harness.hook_calls == []
    finally:
        # The refused redeem's share lock on the practice row is still held by
        # the harness session (the request's own session would have closed).
        harness.session.rollback()
        with harness.engine.begin() as conn:
            conn.execute(
                text("UPDATE platform.practices SET is_active = TRUE WHERE id = :p"),
                {"p": _PRACTICE_ID},
            )


def test_an_unknown_link_is_not_found(harness: _Harness) -> None:
    harness.sign_in(_address("nobody"))
    before = harness.footprint()
    assert harness.redeem("not-a-real-token").status_code == 404
    assert harness.preview("not-a-real-token").status_code == 404
    assert harness.footprint() == before


# --- AC3: a different address needs confirming --------------------------------


def test_a_different_address_without_confirming_is_refused_with_both(harness: _Harness) -> None:
    invited = _address("invited")
    _id, token = harness.invite(invited)
    signed_in = harness.sign_in(_address("other"))
    before = harness.footprint()

    response = harness.redeem(token)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "EMAIL_MISMATCH"
    assert error["details"] == {"invited_email": invited, "account_email": signed_in.email}
    assert harness.footprint() == before, "not even the account is created"
    assert harness.account_id(signed_in) is None
    assert harness.hook_calls == []
    assert harness.preview(token).status_code == 200, "still open"


def test_the_same_address_in_another_case_needs_no_confirming(harness: _Harness) -> None:
    invited = _address("Mixed.Case")
    _id, token = harness.invite(invited)
    harness.sign_in(invited.lower())
    assert harness.redeem(token).status_code == 200


# --- AC4: one practice per account --------------------------------------------


def test_an_account_already_in_a_practice_is_refused_and_nothing_changes(
    harness: _Harness,
) -> None:
    first_address = _address("member")
    _id, first = harness.invite(first_address)
    member = harness.sign_in(first_address)
    assert harness.redeem(first).status_code == 200

    _id, second = harness.invite(_address("again"))
    harness.signed_in["current"] = member
    before = harness.footprint()
    response = harness.redeem(second, confirm_mismatch=True)
    assert response.status_code == 409
    assert _code(response) == "ALREADY_IN_PRACTICE"
    assert harness.footprint() == before
    assert harness.hook_calls == [(_PRACTICE_ID, harness.account_id(member), "joined")]


def test_an_address_another_practice_has_mapped_is_refused(harness: _Harness) -> None:
    mapped = _address("mapped")
    with harness.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform.email_tenant_mappings"
                " (email, tenant_id, practice_id, created_at) VALUES (:e, :p, :p, now())"
            ),
            {"e": mapped, "p": _OTHER_PRACTICE_ID},
        )
    _id, token = harness.invite(mapped)
    harness.sign_in(mapped)
    before = harness.footprint()
    response = harness.redeem(token)
    assert response.status_code == 409
    assert _code(response) == "ALREADY_IN_PRACTICE"
    assert harness.footprint() == before


def test_the_practice_owner_cannot_redeem_into_a_second_practice(harness: _Harness) -> None:
    _id, token = harness.invite(_address("for-owner"))
    owner = harness.sign_in(_OWNER_EMAIL)
    with harness.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform.user_identities (provider, subject_id, user_id, linked_at)"
                " VALUES ('firebase', :s, CAST(:u AS uuid), now())"
            ),
            {"s": owner.subject_id, "u": _OWNER_ID},
        )
    before = harness.footprint()
    response = harness.redeem(token, confirm_mismatch=True)
    assert response.status_code == 409
    assert _code(response) == "ALREADY_IN_PRACTICE"
    assert harness.footprint() == before


# --- AC7: audited with ids only ----------------------------------------------


def test_create_revoke_and_redeem_are_audited_with_ids_only(harness: _Harness) -> None:
    withdrawn = _address("audit-withdrawn")
    withdrawn_id, _ = harness.invite(withdrawn)
    first = harness.as_owner().delete(f"/api/practice/invites/{withdrawn_id}")
    assert first.status_code == 204
    # Withdrawing again changes nothing and records nothing.
    again = harness.as_owner().delete(f"/api/practice/invites/{withdrawn_id}")
    assert again.status_code == 204

    accepted = _address("audit-accepted")
    accepted_id, token = harness.invite(accepted)
    bee = harness.sign_in(_address("audit-redeemer"))
    assert harness.redeem(token, confirm_mismatch=True).status_code == 200
    account = harness.account_id(bee)

    rows = [r for r in harness.audit_rows() if r[2].get("invite_id") in (withdrawn_id, accepted_id)]
    assert rows == [
        (AuditAction.PRACTICE_INVITE_CREATED.value, _OWNER_ID, {"invite_id": withdrawn_id}),
        (AuditAction.PRACTICE_INVITE_REVOKED.value, _OWNER_ID, {"invite_id": withdrawn_id}),
        (AuditAction.PRACTICE_INVITE_CREATED.value, _OWNER_ID, {"invite_id": accepted_id}),
        (
            AuditAction.PRACTICE_INVITE_REDEEMED.value,
            account,
            {"invite_id": accepted_id, "email_matched": False},
        ),
    ]
    flat = repr(rows)
    for address in (withdrawn, accepted, bee.email, "Bo"):
        assert address not in flat
    assert harness.hook_calls == [(_PRACTICE_ID, account, "joined")]
