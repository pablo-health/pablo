# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""How the patient-facing routes reach one practice's sign-in state.

Redeem and refresh are the two routes with no principal to inherit a session
from: redemption runs before any principal exists, and refresh exists to keep
one resolvable. Both therefore have to open their own tenant-scoped session
from the SIGNATURE-VERIFIED token's tenant claim, commit it, and close it —
which, inlined twice, buries the auth logic under transaction plumbing.

This module is that plumbing, named once: a gateway that yields the three
things those routes need inside one practice (the challenge store, the
session store, and somewhere to record the redemption), and owns the
transaction around them.

The seam is also what lets the route tests exercise the real handlers —
uniform 401s, rotation, revocation — against in-memory stores, while the
database implementations are proven where they should be, against a freshly
provisioned practice schema.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Protocol

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..auth.service import TenantContext, get_tenant_context
from ..db import arm_current_patient_id, create_standalone_session, get_db_session
from ..models.audit import AuditAction, ResourceType
from ..repositories.postgres.audit import PostgresAuditRepository
from ..services.audit_service import AuditService
from .db_store import DbPortalAuthStore, DbPortalSessionStore

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from contextlib import AbstractContextManager

    from .store import PortalAuthStore, PortalSessionStore


@dataclass(frozen=True)
class PortalTenantWork:
    """One practice's portal sign-in state, for the span of one request."""

    challenges: PortalAuthStore
    sessions: PortalSessionStore
    #: Record a completed redemption. Separate from the stores because it
    #: writes to the audit log, which has rules of its own — see
    #: :meth:`DbPortalTenantGateway.open`.
    record_redemption: Callable[[str, str], None]
    #: Make everything written so far durable. Called explicitly rather than
    #: on clean exit because redeem has to commit on its FAILURE path too —
    #: the attempt counter a wrong code just bumped is the whole point of
    #: the attempt cap, and rolling it back would make guesses free.
    commit: Callable[[], None]


class PortalTenantGateway(Protocol):
    def open(
        self, tenant: str, request: Request | None
    ) -> AbstractContextManager[PortalTenantWork]:
        """Enter *tenant* and yield its portal sign-in state.

        Raises ``ValueError`` if *tenant* is not a usable schema name — the
        caller turns that into the same uniform 401 as any other refusal,
        rather than letting it surface as a 500 quoting the offending value.
        """
        ...


class DbPortalTenantGateway:
    """The production gateway: a standalone session on the practice schema."""

    @contextmanager
    def open(self, tenant: str, request: Request | None) -> Iterator[PortalTenantWork]:
        session = create_standalone_session(tenant)

        def _record_redemption(patient_id: str, session_jti: str) -> None:
            # The patient principal exists as of the call that leads here,
            # so arm its GUC before the insert: the audit table's patient
            # arm checks the row against ``app.current_patient_id``, and an
            # unarmed insert is refused — a completed authentication with no
            # record of it, which is the one failure § 164.312(b) cannot
            # have.
            arm_current_patient_id(session, patient_id)
            AuditService(PostgresAuditRepository(session)).log_patient_principal_action(
                AuditAction.PATIENT_PORTAL_SESSION_REDEEMED,
                request,
                patient_id=patient_id,
                resource_type=ResourceType.PATIENT,
                resource_id=patient_id,
                # A random session handle — it unlocks nothing on its own,
                # and it is the most identifying thing in the row.
                changes={"session_jti": session_jti},
            )

        try:
            yield PortalTenantWork(
                challenges=DbPortalAuthStore(session, tenant=tenant),
                sessions=DbPortalSessionStore(session),
                record_redemption=_record_redemption,
                commit=session.commit,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def get_portal_tenant_gateway() -> PortalTenantGateway:
    """FastAPI dependency — the indirection route tests override."""
    return DbPortalTenantGateway()


@dataclass(frozen=True)
class PortalStores:
    """The same two stores, for a route that already HAS a tenant session.

    The clinician-facing routes run inside the request's tenant-scoped
    session, armed by the database middleware from their own token, so they
    need the stores but not the transaction management above — the request
    owns that. ``tenant`` is the schema those stores are bound to, which is
    also what goes into a minted token's tenant claim.
    """

    challenges: PortalAuthStore
    sessions: PortalSessionStore
    tenant: str


def get_portal_stores(
    session: Annotated[Session, Depends(get_db_session)],
    tenant_ctx: Annotated[TenantContext, Depends(get_tenant_context)],
) -> PortalStores:
    """Bind the portal stores to the caller's own practice.

    A clinician route never names a schema: it takes the one their token
    resolved to. Refusing an unresolved schema here rather than in each
    route means no route can mint a token whose tenant claim is empty.
    """
    schema = tenant_ctx.practice_schema
    if not schema:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No practice schema resolved for this user.",
        )
    return PortalStores(
        challenges=DbPortalAuthStore(session, tenant=schema),
        sessions=DbPortalSessionStore(session),
        tenant=str(schema),
    )
