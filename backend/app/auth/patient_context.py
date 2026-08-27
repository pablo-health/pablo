# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The patient principal: a second, separate authenticated identity.

Pablo's original principal is the clinician — a Firebase (or OIDC) subject
resolved to a Pablo user id and a practice, carried as
:class:`~app.auth.service.TenantContext`. Patient-facing surfaces (intake,
the between-session companion) need an identity that is *not* that: a
patient authenticates to their own record inside one practice, and must
never pick up a clinician's reach into the rest of the tenant.

This module defines that principal and the seam it is resolved through:

* :class:`PatientContext` — the resolved principal (patient, tenant, how
  they proved it, how strongly).
* :class:`PatientCredential` — an inbound credential normalized off the
  transport, so a resolver never sees an HTTP request.
* :class:`PatientPrincipalResolver` — the protocol an authentication
  front door implements, and :class:`PatientResolverRegistry`, keyed by
  credential kind, that holds them.
* :func:`get_patient_context` — the FastAPI dependency patient routes
  depend on, which arms the tenant ``search_path`` and the patient RLS
  GUC for the request.

**One principal, many front doors.** Magic-link plus SMS step-up is the
first way a patient proves who they are; an embedded widget handing off a
host-signed assertion, an enterprise OIDC/SAML SSO, and SMART-on-FHIR
patient launch are all plausible later ones. They are adapters on this
seam, so nothing here names a vendor or an identity-provider type — the
protocol takes an opaque credential and returns a principal. Baking
"Firebase" or "JWT" into the signature now would mean rewriting every
patient route to add the second front door.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from fastapi import Depends, HTTPException, Request, status

from ..db import arm_current_patient_id, get_db_session, set_tenant_schema

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

__all__ = [
    "AuthStrength",
    "PatientContext",
    "PatientCredential",
    "PatientPrincipalResolver",
    "PatientResolverRegistry",
    "get_patient_context",
    "get_patient_resolver_registry",
    "patient_resolver_registry",
]

_EMPTY_PARAMETERS: Mapping[str, str] = MappingProxyType({})


class AuthStrength(StrEnum):
    """How strongly the patient proved their identity.

    ``SINGLE_FACTOR`` is possession of one thing — an unredeemed invite
    link that arrived by email, say. ``STEPPED_UP`` means a second factor
    was cleared (the SMS one-time code the magic-link flow redeems
    through). Routes that expose clinical content should require
    ``STEPPED_UP``; a link forwarded to the wrong person is a single
    factor in someone else's hands.

    This is recorded rather than enforced here: the dependency resolves a
    principal, and each route decides what strength its content needs.
    """

    SINGLE_FACTOR = "single_factor"
    STEPPED_UP = "stepped_up"


@dataclass(frozen=True)
class PatientContext:
    """An authenticated patient, scoped to exactly one practice.

    The patient analog of :class:`~app.auth.service.TenantContext`, and
    deliberately not a subclass of it: the two principals share no
    substitutable behaviour, and a common base class is how a patient
    principal would eventually satisfy a dependency that meant to ask for
    a clinician.

    ``practice_schema`` is the Postgres schema this patient's record
    lives in. It is validated as an identifier before it reaches SQL (see
    ``app.db.set_tenant_schema``), so a resolver returning an
    attacker-influenced value cannot inject.
    """

    patient_id: str
    practice_schema: str
    credential_kind: str
    auth_strength: AuthStrength


@dataclass(frozen=True)
class PatientCredential:
    """An inbound credential, normalized off whatever transport carried it.

    Resolvers receive this rather than a ``Request`` so that a front door
    can be exercised without an HTTP layer, and so that a resolver cannot
    quietly reach for some other part of the request to make its decision.

    ``kind`` is the registry key — the ``Authorization`` scheme for
    header-borne credentials (``"bearer"``), or whatever name a future
    non-header front door registers under. ``value`` is the raw
    credential material. ``parameters`` carries transport extras a front
    door may need (an audience hint, a launch context) without widening
    the signature every time one appears.
    """

    kind: str
    value: str
    parameters: Mapping[str, str] = field(default=_EMPTY_PARAMETERS)


@runtime_checkable
class PatientPrincipalResolver(Protocol):
    """A front door: turns one kind of credential into a patient principal.

    ``credential_kind`` is what the resolver is registered under.
    ``resolve`` returns ``None`` for "not mine, or not valid" — it does
    not raise to signal rejection, because a resolver's failure must not
    be able to choose the response the caller sees. The dependency turns
    every unresolved credential into an identical 401.
    """

    credential_kind: str

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        raise NotImplementedError


class PatientResolverRegistry:
    """The resolvers available to :func:`get_patient_context`, by kind.

    Several resolvers may share a kind — a companion session token and a
    future SSO assertion both arrive as ``bearer`` — so each kind holds an
    ordered list and the first resolver to claim the credential wins.
    Registration order is therefore precedence order.
    """

    def __init__(self) -> None:
        self._by_kind: dict[str, list[PatientPrincipalResolver]] = {}

    def register(self, resolver: PatientPrincipalResolver) -> None:
        self._by_kind.setdefault(resolver.credential_kind, []).append(resolver)

    def clear(self) -> None:
        """Drop every registered resolver. For test isolation."""
        self._by_kind.clear()

    def resolvers_for(self, kind: str) -> tuple[PatientPrincipalResolver, ...]:
        return tuple(self._by_kind.get(kind, ()))

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        """Return the first principal a registered resolver claims.

        A resolver that raises is treated as a rejection and the next one
        is tried. That is deliberate: an adapter reaching a down identity
        provider should not be able to turn a patient's request into a
        500 that discloses which front door broke, and it must not skip
        the resolvers registered behind it. The exception type is logged
        without the credential material.
        """
        for resolver in self._by_kind.get(credential.kind, ()):
            try:
                context = resolver.resolve(credential)
            except Exception:
                logger.warning(
                    "Patient principal resolver failed",
                    extra={
                        "credential_kind": credential.kind,
                        "resolver": type(resolver).__name__,
                    },
                    exc_info=True,
                )
                continue
            if context is not None:
                return context
        return None


# Process-wide registry. Front doors register at import/bootstrap time;
# `get_patient_resolver_registry` is the indirection tests override.
patient_resolver_registry = PatientResolverRegistry()


def get_patient_resolver_registry() -> PatientResolverRegistry:
    """Return the active registry (a FastAPI-overridable dependency)."""
    return patient_resolver_registry


def _credential_from_request(request: Request) -> PatientCredential | None:
    """Normalize the request's ``Authorization`` header into a credential.

    Returns ``None`` when there is nothing to resolve. Non-header front
    doors register their own kind and are extracted here when they land;
    today every patient credential is header-borne.
    """
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if not scheme or not value.strip():
        return None
    return PatientCredential(kind=scheme.lower(), value=value.strip())


def _unauthenticated() -> HTTPException:
    """The single 401 every failure to resolve a patient produces.

    Uniform on purpose: "no such patient", "expired token", "signature
    bad" and "no resolver for this kind" are indistinguishable to the
    caller, so an unauthenticated probe learns nothing about which
    patients or which front doors exist.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": {
                "code": "PATIENT_NOT_AUTHENTICATED",
                "message": "Not authenticated",
                "details": {},
            }
        },
    )


def get_patient_context(
    request: Request,
    registry: PatientResolverRegistry = Depends(get_patient_resolver_registry),
) -> PatientContext:
    """FastAPI dependency: resolve the caller to an authenticated patient.

    Patient routes depend on this and on nothing clinician-shaped. It:

    1. Refuses outright if the credential already verified as a
       *clinician* — see below.
    2. Normalizes the inbound credential and asks the registry for a
       principal; anything unresolved is a 401.
    3. Arms the tenant ``search_path`` for the request's session, which
       the middleware could not do (it resolves the schema from a
       clinician token, and a patient does not carry one).
    4. Arms the transaction-local ``app.current_patient_id`` GUC that
       patient-scoped RLS policies read.

    **Why step 1.** A clinician bearer token is also ``kind="bearer"``,
    so hard separation would otherwise rest on every present and future
    patient resolver remembering to reject clinician tokens — one
    forgetful adapter away from a clinician authenticating as whichever
    patient id their token happened to mention.
    ``DatabaseSessionMiddleware`` has already verified and cached the
    identity when the token is a clinician's, so the check costs a
    ``getattr`` rather than a second round trip to the identity provider.

    The reverse direction needs no guard: a patient credential is not a
    Firebase or OIDC token, so the clinician dependencies reject it in
    their verifiers. Both directions are covered by tests.

    Raises:
        HTTPException: 401 if no patient principal can be resolved.
    """
    if getattr(request.state, "verified_identity", None) is not None:
        logger.warning("Clinician credential presented to a patient route")
        raise _unauthenticated()

    credential = _credential_from_request(request)
    if credential is None:
        raise _unauthenticated()

    context = registry.resolve(credential)
    if context is None:
        raise _unauthenticated()

    session = get_db_session()
    set_tenant_schema(session, context.practice_schema)
    arm_current_patient_id(session, context.patient_id)
    return context
