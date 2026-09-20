# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""How account recovery reaches one practice's chart and sign-in state.

The sibling of :mod:`app.portal.tenant_gateway`, for the one other route
with no principal to inherit a transaction from. Recovery arrives with a
slug out of a URL and nothing else, so it has to resolve a practice from the
public directory, open a session on that practice's schema, do four
unrelated things inside it — find a chart by email, read the sign-in state,
mint an invitation, record the event — and commit.

Inlined, that buries the security argument under transaction plumbing, which
is exactly what happened to redeem and refresh before the gateway there
existed. So the plumbing is named once here and the route reads as its own
decision tree.

The seam is also what lets the recovery route's tests exercise the real
handler — the uniform 202, the grant check, the single-match rule, the
audit — against in-memory stores, while the database implementations are
proven against a freshly provisioned practice schema.

**Nothing here decides anything.** Every refusal — no such practice, no
matching chart, two matching charts, no active grant — is the ROUTE's, and
this module only reports what it found. A gateway that returned "no" for two
different reasons would put half the policy somewhere the route's docstring
does not describe it.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import func, select

from ..db import arm_current_patient_id, create_standalone_session
from ..db.models import PatientRow
from ..models.audit import AuditAction, ResourceType
from ..repositories.postgres.audit import PostgresAuditRepository
from ..services.audit_service import AuditService
from .db_store import DbPortalAuthStore, DbPortalSessionStore
from .directory import practice_schema_for_slug

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from contextlib import AbstractContextManager

    from fastapi import Request

    from .store import PortalAuthStore, PortalSessionStore


@dataclass(frozen=True)
class RecoveryTarget:
    """The three fields minting an invitation needs, and nothing else.

    A chart row read on an unauthenticated path is exactly where a whole ORM
    object should not be carried around: the row has a diagnosis on it. This
    carries an id and the two delivery channels, so nothing else can be
    reached by accident from anywhere downstream.
    """

    patient_id: str
    email: str | None
    phone: str | None


@dataclass(frozen=True)
class RecoveryWork:
    """One practice's recovery state, for the span of one request."""

    #: The one live chart in this practice with the given address, or
    #: ``None`` — which covers no match AND more than one. The caller must
    #: not be able to tell those apart and neither can this.
    find_patient: Callable[[str], RecoveryTarget | None]
    challenges: PortalAuthStore
    sessions: PortalSessionStore
    #: Record that this patient asked for their access back. Separate from
    #: the stores because it writes to the audit log, which has rules of its
    #: own — see :meth:`DbRecoveryGateway.open`.
    record_request: Callable[[str, str], None]
    commit: Callable[[], None]


class RecoveryGateway(Protocol):
    def resolve(self, slug: str) -> str | None:
        """The schema this practice's patients live in, or ``None``.

        ``None`` for an unknown slug, a portal that is off, and a practice
        that is inactive or deleted — one answer for all of them.
        """
        ...

    def open(self, schema: str, request: Request | None) -> AbstractContextManager[RecoveryWork]:
        """Enter *schema* and yield its recovery state.

        Raises ``ValueError`` if *schema* is not a usable name. The caller
        swallows everything into the same 202, so that is a log line rather
        than a second answer.
        """
        ...


class DbRecoveryGateway:
    """The production gateway: a standalone session on the practice schema."""

    def resolve(self, slug: str) -> str | None:
        return practice_schema_for_slug(slug)

    @contextmanager
    def open(self, schema: str, request: Request | None) -> Iterator[RecoveryWork]:
        session = create_standalone_session(schema)

        def _find_patient(email: str) -> RecoveryTarget | None:
            # Three columns rather than the row, the same posture
            # ``PATIENT_FACING_COLUMNS`` takes: the caller here is
            # unauthenticated, and a whole-row read on this path would put a
            # diagnosis in scope one attribute access from a response.
            #
            # Two rows are taken and two are refused, which is why this
            # cannot be written as a ``LIMIT 1``.
            #
            # Read directly rather than through
            # ``PatientRepository.find_by_email``: that one joins
            # ``patient_clinicians`` to bound the read to one clinician's
            # caseload, which is right for a caller who IS a clinician and
            # wrong here, where the caller is nobody and the bound is the
            # practice schema itself.
            rows = session.execute(
                select(PatientRow.id, PatientRow.email, PatientRow.phone)
                .where(
                    func.lower(PatientRow.email) == email.lower(),
                    PatientRow.deleted_at.is_(None),
                    PatientRow.status != "pending",
                )
                .limit(2)
            ).all()
            if len(rows) != 1:
                return None
            row = rows[0]
            return RecoveryTarget(patient_id=row.id, email=row.email, phone=row.phone)

        def _record_request(patient_id: str, invite_jti: str) -> None:
            # The patient is the actor, so the GUC the audit table's patient
            # arm checks has to be armed before the insert — same ordering
            # as the redemption gateway, and for the same reason: an unarmed
            # insert is refused, and a credential event with no record of it
            # is the gap § 164.312(b) cannot have.
            arm_current_patient_id(session, patient_id)
            AuditService(PostgresAuditRepository(session)).log_patient_principal_action(
                AuditAction.PATIENT_ACCESS_RECOVERY_REQUESTED,
                request,
                patient_id=patient_id,
                resource_type=ResourceType.PATIENT,
                resource_id=patient_id,
                # A random handle to the challenge row. The token, the link,
                # the code and the address are recorded nowhere.
                changes={"invite_jti": invite_jti},
            )

        try:
            yield RecoveryWork(
                find_patient=_find_patient,
                challenges=DbPortalAuthStore(session, tenant=schema),
                sessions=DbPortalSessionStore(session),
                record_request=_record_request,
                commit=session.commit,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def get_recovery_gateway() -> RecoveryGateway:
    """FastAPI dependency — the indirection the route tests override."""
    return DbRecoveryGateway()


__all__ = [
    "DbRecoveryGateway",
    "RecoveryGateway",
    "RecoveryTarget",
    "RecoveryWork",
    "get_recovery_gateway",
]
