# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The authorisation that lets Pablo apply to panels on a clinician's behalf.

Bug classes covered:

* **A missing document waving work through.** A deployment that bundles no
  authorisation has nobody to authorise, and the flow has to be OFF rather than
  open. The failure mode worth guarding is the other reading — treating absent
  as satisfied — because it fails silently and in the permissive direction.
* **A signature that survives an edit to what was signed.** The whole value of
  the record is that it holds the words she saw. If the text is read back from
  disk at answer time, revising the file quietly rewrites history.
* **A superseded signature still counting as permission.** She agreed to the
  old wording. That is history, not consent to the new one, and the two have to
  be distinguishable from never having signed at all.
* **A withdrawal that erases the record.** Revoking has to end the authority
  without unmaking the fact that it once held — otherwise nobody can answer for
  the period when it did.
* **Signing something she was not shown.** The version comes back in the
  payload precisely so a document published mid-read cannot be signed blind.

Runs the real router against a real provisioned practice schema, with the
document directory pointed at a tmpdir — the real documents live in the
managed deployment's overlay, not in this repository. The signature table is
per-tenant and row-secured, so the schema and the armed principal are part of
what makes a read return anything at all.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from app.api_errors import register_exception_handlers
from app.auth.route_access import subscription_exempt
from app.auth.service import get_current_user, get_tenant_context
from app.credentialing import authorization
from app.db import arm_current_user_id, get_db_session, set_tenant_schema
from app.db.platform_models import PayerAuthorizationRow
from app.db.provisioning import create_practice_schema
from app.models import User
from app.routes import credentialing as credentialing_routes
from app.services.audit_service import AuditService, InMemoryAuditRepository, get_audit_service
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

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
_SCHEMA = f"practice_test_payerauth_{_SUFFIX}"

_USER_ID = str(uuid.uuid4())
_OTHER_USER_ID = str(uuid.uuid4())
_URL = "/api/credentialing/payer-authorization"

_V1 = "2026-01-01"
_V2 = "2026-09-13"
_V1_TEXT = "# Payer authorisation\n\nVersion one, as she first saw it.\n"
_V2_TEXT = "# Payer authorisation\n\nVersion two, with the wording revised.\n"


def _user() -> User:
    now = datetime.now(UTC)
    return User(
        id=_USER_ID,
        email="therapist@example.com",
        name="Test Therapist",
        created_at=now,
        baa_accepted_at=now,
        baa_version="2024-01-01",
    )


@pytest.fixture
def documents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty document directory. Tests that want versions write them."""
    directory = tmp_path / "payer_authorizations"
    directory.mkdir()
    monkeypatch.setattr(authorization, "AUTHORIZATION_DIR", directory)
    return directory


def _publish(documents: Path, version: str, text: str) -> None:
    (documents / f"PAYER-AUTH-{version}.md").write_text(text)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    """The real database, migrated, with a practice schema of this module's own."""
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    create_practice_schema(eng, _SCHEMA)
    yield eng
    with eng.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE'))
        conn.commit()
    eng.dispose()


@pytest.fixture
def harness(engine: Engine, documents: Path) -> Iterator[dict[str, Any]]:
    session = Session(engine)
    set_tenant_schema(session, _SCHEMA)
    arm_current_user_id(session, _USER_ID)
    audit_repo = InMemoryAuditRepository()
    audit = AuditService(audit_repo)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(credentialing_routes.router)
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_tenant_context] = lambda: None
    app.dependency_overrides[subscription_exempt] = lambda: None
    app.dependency_overrides[get_db_session] = lambda: session
    app.dependency_overrides[get_audit_service] = lambda: audit
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield {
            "client": client,
            "session": session,
            "documents": documents,
            "audit": audit_repo,
        }
    finally:
        # The table outlives each test, and now outlives this module too: it
        # is in platform rather than in this module's own schema. The session
        # is armed as this harness's clinician, so the row policy scopes the
        # delete to her rows and leaves every other module's alone.
        session.rollback()
        session.execute(text("DELETE FROM platform.payer_authorizations"))
        session.commit()
        session.close()


def _sign(client: TestClient, version: str, name: str = "Ana Rivera") -> Any:
    return client.post(_URL, json={"version": version, "signed_name": name, "accepted": True})


class TestADeploymentThatBundlesNothing:
    def test_reports_unavailable_rather_than_merely_unsigned(self, harness: dict[str, Any]) -> None:
        """The screen has to be able to show nothing at all to a self-hoster."""
        body = harness["client"].get(_URL).json()

        assert body["available"] is False
        assert body["signed"] is False

    def test_nothing_can_be_signed_against_it(self, harness: dict[str, Any]) -> None:
        assert _sign(harness["client"], _V1).status_code == 404

    def test_the_gate_is_shut_not_open(self, harness: dict[str, Any]) -> None:
        """Absent must never read as satisfied — that fails in the permissive direction."""
        assert authorization.in_force(harness["session"], _USER_ID) is None


class TestBeforeSheSigns:
    def test_the_current_version_is_offered(self, harness: dict[str, Any]) -> None:
        _publish(harness["documents"], _V1, _V1_TEXT)

        body = harness["client"].get(_URL).json()

        assert body == {
            "available": True,
            "current_version": _V1,
            "signed": False,
            "signed_version": None,
            "signed_at": None,
            "signed_name": None,
            "superseded": False,
        }

    def test_the_newest_version_wins_when_several_are_bundled(
        self, harness: dict[str, Any]
    ) -> None:
        _publish(harness["documents"], _V1, _V1_TEXT)
        _publish(harness["documents"], _V2, _V2_TEXT)

        assert harness["client"].get(_URL).json()["current_version"] == _V2

    def test_she_can_read_it_before_deciding(self, harness: dict[str, Any]) -> None:
        _publish(harness["documents"], _V1, _V1_TEXT)

        assert harness["client"].get(f"{_URL}/document").json() == _V1_TEXT

    def test_an_unticked_box_is_not_a_signature(self, harness: dict[str, Any]) -> None:
        _publish(harness["documents"], _V1, _V1_TEXT)

        response = harness["client"].post(
            _URL, json={"version": _V1, "signed_name": "Ana Rivera", "accepted": False}
        )

        assert response.status_code == 400

    def test_a_version_this_deployment_does_not_have_cannot_be_signed(
        self, harness: dict[str, Any]
    ) -> None:
        """The version rides in the payload so a mid-read publish cannot be signed blind."""
        _publish(harness["documents"], _V1, _V1_TEXT)

        assert _sign(harness["client"], "2099-01-01").status_code == 404


class TestSigning:
    def test_it_takes_effect_and_says_under_what(self, harness: dict[str, Any]) -> None:
        _publish(harness["documents"], _V1, _V1_TEXT)

        body = _sign(harness["client"], _V1).json()

        assert body["signed"] is True
        assert body["signed_version"] == _V1
        assert body["signed_name"] == "Ana Rivera"

    def test_the_words_she_saw_are_kept_beside_the_signature(self, harness: dict[str, Any]) -> None:
        """The record has to survive an edit to the file it came from.

        This is the whole reason the text is copied rather than referenced: a
        payer or a board asking what authority we claimed needs the wording she
        agreed to, not whatever the file says today.
        """
        _publish(harness["documents"], _V1, _V1_TEXT)
        _sign(harness["client"], _V1)

        (harness["documents"] / f"PAYER-AUTH-{_V1}.md").write_text("Something else entirely.")

        row = harness["session"].query(PayerAuthorizationRow).one()
        assert row.full_text == _V1_TEXT

    def test_the_name_is_stored_as_she_typed_it(self, harness: dict[str, Any]) -> None:
        """Refusing "Dr. A. Rivera" against a record saying "Ana Rivera" would be
        the product arguing with a signature."""
        _publish(harness["documents"], _V1, _V1_TEXT)

        _sign(harness["client"], _V1, name="Dr. A. Rivera, LCSW")

        assert harness["session"].query(PayerAuthorizationRow).one().signed_name == (
            "Dr. A. Rivera, LCSW"
        )

    def test_it_is_audited(self, harness: dict[str, Any]) -> None:
        """The moment Pablo acquires authority to act for her with third parties."""
        _publish(harness["documents"], _V1, _V1_TEXT)

        _sign(harness["client"], _V1)

        actions = [entry.action for entry in harness["audit"].list_for_user(_USER_ID)]
        assert "payer_authorization_signed" in actions

    def test_the_gate_opens(self, harness: dict[str, Any]) -> None:
        _publish(harness["documents"], _V1, _V1_TEXT)
        _sign(harness["client"], _V1)

        assert authorization.in_force(harness["session"], _USER_ID) is not None

    def test_it_opens_for_her_alone(self, harness: dict[str, Any]) -> None:
        _publish(harness["documents"], _V1, _V1_TEXT)
        _sign(harness["client"], _V1)

        assert authorization.in_force(harness["session"], _OTHER_USER_ID) is None


class TestANewerVersion:
    def test_supersedes_her_signature_rather_than_inheriting_it(
        self, harness: dict[str, Any]
    ) -> None:
        """She agreed to the old wording. That is history, not consent to the new."""
        _publish(harness["documents"], _V1, _V1_TEXT)
        _sign(harness["client"], _V1)
        _publish(harness["documents"], _V2, _V2_TEXT)

        body = harness["client"].get(_URL).json()

        assert body["signed"] is False
        assert body["superseded"] is True
        assert body["signed_version"] == _V1
        assert body["current_version"] == _V2

    def test_shuts_the_gate(self, harness: dict[str, Any]) -> None:
        _publish(harness["documents"], _V1, _V1_TEXT)
        _sign(harness["client"], _V1)
        _publish(harness["documents"], _V2, _V2_TEXT)

        assert authorization.in_force(harness["session"], _USER_ID) is None

    def test_signing_it_adds_a_row_rather_than_replacing_the_old_one(
        self, harness: dict[str, Any]
    ) -> None:
        """What authority did you hold in March is answerable only if March survives."""
        _publish(harness["documents"], _V1, _V1_TEXT)
        _sign(harness["client"], _V1)
        _publish(harness["documents"], _V2, _V2_TEXT)
        _sign(harness["client"], _V2)

        rows = harness["session"].query(PayerAuthorizationRow).all()
        assert sorted(row.version for row in rows) == [_V1, _V2]
        assert {row.full_text for row in rows} == {_V1_TEXT, _V2_TEXT}

    def test_the_earlier_wording_is_still_readable(self, harness: dict[str, Any]) -> None:
        _publish(harness["documents"], _V1, _V1_TEXT)
        _publish(harness["documents"], _V2, _V2_TEXT)

        assert harness["client"].get(f"{_URL}/document", params={"version": _V1}).json() == _V1_TEXT


class TestWithdrawing:
    def test_it_shuts_the_gate(self, harness: dict[str, Any]) -> None:
        _publish(harness["documents"], _V1, _V1_TEXT)
        _sign(harness["client"], _V1)

        body = harness["client"].delete(_URL).json()

        assert body["signed"] is False
        assert authorization.in_force(harness["session"], _USER_ID) is None

    def test_it_does_not_unmake_the_fact_that_she_signed(self, harness: dict[str, Any]) -> None:
        """Nobody could answer for the period the authority held if it erased itself."""
        _publish(harness["documents"], _V1, _V1_TEXT)
        _sign(harness["client"], _V1)

        harness["client"].delete(_URL)

        row = harness["session"].query(PayerAuthorizationRow).one()
        assert row.revoked_at is not None
        assert row.full_text == _V1_TEXT
        assert row.signed_name == "Ana Rivera"

    def test_it_sweeps_superseded_signatures_too(self, harness: dict[str, Any]) -> None:
        """ "Stop acting for me" read as narrowly as possible is not an answer."""
        _publish(harness["documents"], _V1, _V1_TEXT)
        _sign(harness["client"], _V1)
        _publish(harness["documents"], _V2, _V2_TEXT)
        _sign(harness["client"], _V2)

        harness["client"].delete(_URL)

        rows = harness["session"].query(PayerAuthorizationRow).all()
        assert all(row.revoked_at is not None for row in rows)

    def test_withdrawing_twice_is_not_an_error(self, harness: dict[str, Any]) -> None:
        """She is asking for a state, and the honest answer to "already" is that state."""
        _publish(harness["documents"], _V1, _V1_TEXT)
        _sign(harness["client"], _V1)

        harness["client"].delete(_URL)
        second = harness["client"].delete(_URL)

        assert second.status_code == 200
        assert second.json()["signed"] is False

    def test_withdrawing_when_she_never_signed_is_not_an_error(
        self, harness: dict[str, Any]
    ) -> None:
        _publish(harness["documents"], _V1, _V1_TEXT)

        assert harness["client"].delete(_URL).status_code == 200

    def test_she_can_sign_again_afterwards(self, harness: dict[str, Any]) -> None:
        """Withdrawal is not a one-way door; re-signing is a real sequence."""
        _publish(harness["documents"], _V1, _V1_TEXT)
        _sign(harness["client"], _V1)
        harness["client"].delete(_URL)

        body = _sign(harness["client"], _V1).json()

        assert body["signed"] is True
        assert len(harness["session"].query(PayerAuthorizationRow).all()) == 2

    def test_it_is_audited(self, harness: dict[str, Any]) -> None:
        """When the authority ENDED is the half a complaint usually turns on."""
        _publish(harness["documents"], _V1, _V1_TEXT)
        _sign(harness["client"], _V1)

        harness["client"].delete(_URL)

        actions = [entry.action for entry in harness["audit"].list_for_user(_USER_ID)]
        assert "payer_authorization_revoked" in actions

    def test_a_no_op_withdrawal_is_not_audited_as_an_event(self, harness: dict[str, Any]) -> None:
        """Nothing happened, so the log should not say something did."""
        _publish(harness["documents"], _V1, _V1_TEXT)

        harness["client"].delete(_URL)

        actions = [entry.action for entry in harness["audit"].list_for_user(_USER_ID)]
        assert "payer_authorization_revoked" not in actions


class TestTheDocumentDirectory:
    def test_a_version_cannot_walk_out_of_it(self, harness: dict[str, Any]) -> None:
        """Read through the discovered map, never by joining input onto a path."""
        _publish(harness["documents"], _V1, _V1_TEXT)

        response = harness["client"].get(
            f"{_URL}/document", params={"version": "../../../etc/passwd"}
        )

        assert response.status_code == 404

    def test_a_file_that_is_not_a_dated_version_is_ignored(self, harness: dict[str, Any]) -> None:
        """A draft left in the directory must not become the version in force."""
        _publish(harness["documents"], _V1, _V1_TEXT)
        (harness["documents"] / "PAYER-AUTH-draft.md").write_text("Not ready.")

        assert harness["client"].get(_URL).json()["current_version"] == _V1


def _publish_agreement(documents: Path, version: str, text: str) -> None:
    (documents / f"SERVICES-AGREEMENT-{version}.md").write_text(text)


class TestTheTwoDocuments:
    """The services agreement and the credentialing authorisation are separate.

    Only the second has text today. The column and the discovery exist now
    because adding them later would mean a migration plus a backfill that has
    to guess which document a live signature had been — and because the
    permissive failure is the one that matters: a signed commercial agreement
    reading as permission to sign her name to a payer's form.
    """

    def test_a_signature_records_which_document_it_is_of(self, harness: dict[str, Any]) -> None:
        _publish(harness["documents"], _V1, _V1_TEXT)

        _sign(harness["client"], _V1)

        row = harness["session"].query(PayerAuthorizationRow).one()
        assert row.kind == authorization.CREDENTIALING_AUTHORIZATION

    def test_each_kind_carries_its_own_version_series(self, harness: dict[str, Any]) -> None:
        """Revising one document must not move the other's version in force."""
        _publish(harness["documents"], _V1, _V1_TEXT)
        _publish_agreement(harness["documents"], _V2, "The commercial terms.")

        assert authorization.current_version(authorization.CREDENTIALING_AUTHORIZATION) == _V1
        assert authorization.current_version(authorization.SERVICES_AGREEMENT) == _V2

    def test_one_document_is_not_read_as_the_other(self, harness: dict[str, Any]) -> None:
        """The permissive failure: a signed agreement counting as signing authority."""
        _publish_agreement(harness["documents"], _V1, "The commercial terms.")
        _publish(harness["documents"], _V1, _V1_TEXT)
        authorization.sign(
            harness["session"],
            _USER_ID,
            version=_V1,
            signed_name="Ana Rivera",
            at=datetime.now(UTC),
            kind=authorization.SERVICES_AGREEMENT,
        )

        assert (
            authorization.in_force(
                harness["session"], _USER_ID, authorization.CREDENTIALING_AUTHORIZATION
            )
            is None
        )
        assert harness["client"].get(_URL).json()["signed"] is False

    def test_a_version_of_the_wrong_kind_cannot_be_signed(self, harness: dict[str, Any]) -> None:
        """Same date, other document — not a version of this one."""
        _publish_agreement(harness["documents"], _V1, "The commercial terms.")

        assert _sign(harness["client"], _V1).status_code == 404

    def test_withdrawal_sweeps_both(self, harness: dict[str, Any]) -> None:
        """ "Stop acting for me" is not read as narrowly as it could be."""
        _publish(harness["documents"], _V1, _V1_TEXT)
        _publish_agreement(harness["documents"], _V1, "The commercial terms.")
        _sign(harness["client"], _V1)
        authorization.sign(
            harness["session"],
            _USER_ID,
            version=_V1,
            signed_name="Ana Rivera",
            at=datetime.now(UTC),
            kind=authorization.SERVICES_AGREEMENT,
        )

        harness["client"].delete(_URL)

        rows = authorization.signatures_for(harness["session"], _USER_ID)
        assert len(rows) == 2
        assert all(row.revoked_at is not None for row in rows)

    def test_history_can_be_read_per_document_or_whole(self, harness: dict[str, Any]) -> None:
        _publish(harness["documents"], _V1, _V1_TEXT)
        _publish_agreement(harness["documents"], _V1, "The commercial terms.")
        _sign(harness["client"], _V1)
        authorization.sign(
            harness["session"],
            _USER_ID,
            version=_V1,
            signed_name="Ana Rivera",
            at=datetime.now(UTC),
            kind=authorization.SERVICES_AGREEMENT,
        )

        everything = authorization.signatures_for(harness["session"], _USER_ID)
        just_the_authorization = authorization.signatures_for(
            harness["session"], _USER_ID, authorization.CREDENTIALING_AUTHORIZATION
        )

        assert len(everything) == 2
        assert [row.kind for row in just_the_authorization] == [
            authorization.CREDENTIALING_AUTHORIZATION
        ]
