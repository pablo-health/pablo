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

from ..db import (
    DEFAULT_PRACTICE_SCHEMA,
    arm_current_patient_id,
    get_db_session,
    set_tenant_schema,
)

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

    **Scope every query by ``patient_id`` from here, and do not accept a
    patient id from the client.** A route shaped
    ``GET /patient/record/{patient_id}`` has an IDOR surface by
    construction: it invites an id the caller controls, and then the only
    thing standing between two patients is that somebody remembered to
    compare it. A route that reads the id off this context instead has
    nothing to compare and nothing to forget — the principal already
    says who is calling. Row-level security backs this up rather than
    replacing it, and note it backs up *nothing* in a single-practice
    deployment (see ``PATIENT_READABLE_TABLES``), so the route shape is
    the primary control, not the fallback.
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

    **Contract for implementers, and both halves are load-bearing:**

    1. **Return ``None`` to reject.** Raising is for infrastructure
       failure only, and it is not a soft rejection: an exception aborts
       resolution for the whole request, so the resolvers registered
       behind you never see the credential and the caller gets a 401.
       A front door that raises on a forged signature — rather than
       returning ``None`` — therefore denies credentials that a later
       resolver would legitimately have accepted. Reject with ``None``;
       raise only when you genuinely could not decide.
    2. **A patient credential must fail every clinician verifier.** The
       reverse separation direction (a patient token not satisfying
       ``get_current_user_id`` / ``get_tenant_context``) currently holds
       only because no patient credential format exists yet. If a future
       front door mints something a clinician verifier accepts — Firebase
       custom tokens being the obvious trap — then ``verify_token`` would
       accept it and the clinician auto-provision path would treat the
       patient as a user. Whatever you mint must be structurally
       unacceptable to those verifiers, not merely different in practice.

    Resolvers should also avoid blocking the event loop:
    ``get_patient_context`` is an async dependency (it has to be — see its
    docstring), so a resolver doing network I/O should not do it
    synchronously on the calling thread.
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

        A resolver that raises **aborts resolution entirely** — the
        resolvers behind it are not tried, and the caller gets the same
        uniform 401 as any other failure.

        That asymmetry is the point. ``None`` means "not my credential";
        an exception means "I could not decide". Letting a
        could-not-decide fall through converts it into "someone weaker
        may decide", which hands an attacker an auth-strength downgrade:
        register a ``STEPPED_UP`` front door ahead of a
        ``SINGLE_FACTOR`` one — the arrangement :class:`AuthStrength`
        explicitly anticipates — and anyone who can make the first one
        raise (DoS its provider, or feed input that trips a parse error
        before the signature check) is served by the weaker door instead.
        Aborting costs a resolvable credential nothing that matters: the
        request fails closed, and every property the fall-through was
        protecting is preserved. There is still no 500, still no
        disclosure of which door broke.

        Only the exception's *type name* is logged — no message, no
        traceback. Identity-provider SDKs routinely put the offending
        token into the exception text (JWT libraries quote the token they
        could not decode; HTTP clients attach request bodies), so
        ``exc_info=True`` here would be a credential-to-logs channel in a
        codebase whose rule is that neither PHI nor secrets reach a log.
        """
        for resolver in self._by_kind.get(credential.kind, ()):
            try:
                context = resolver.resolve(credential)
            except Exception as exc:
                logger.warning(
                    "Patient principal resolver failed; aborting resolution",
                    extra={
                        "credential_kind": credential.kind,
                        "resolver": type(resolver).__name__,
                        "error_type": type(exc).__name__,
                    },
                )
                return None
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


def _is_tenant_schema(schema: str) -> bool:
    """Is this the schema of an actual practice, rather than a shared one?

    ``platform``, ``public`` and ``practice`` (the provisioning template)
    are all valid identifiers and all wrong answers for a patient request.
    A per-practice schema is ``practice_<id>``.
    """
    return schema.startswith(f"{DEFAULT_PRACTICE_SCHEMA}_")


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


async def get_patient_context(
    request: Request,
    registry: PatientResolverRegistry = Depends(get_patient_resolver_registry),
) -> PatientContext:
    """FastAPI dependency: resolve the caller to an authenticated patient.

    **This must stay `async`.** FastAPI runs a *sync* dependency in a
    throwaway anyio threadpool worker whose context is a copy, so a
    ``ContextVar.set()`` inside one is discarded the moment it returns.
    ``set_tenant_schema`` below writes ``_current_tenant_schema``, and the
    pool-checkout listener re-applies ``search_path`` from exactly that
    ContextVar on every connection this request later acquires. As a sync
    dependency, the schema would be lost: the in-connection ``SET`` holds
    only until the first mid-request commit releases the connection, and
    the next checkout would then stamp whatever the middleware left —
    ``DEFAULT_PRACTICE_SCHEMA``, the shared template — while the patient
    GUC below stayed correctly armed off ``Session.info``. The second half
    of the request would read and write the template schema under a live
    patient identity. The GUC survives that hop because it has a
    ``Session.info`` channel (see ``arm_current_user_id``); the schema has
    no such channel, so this dependency has to run on the event loop
    instead. A regression test covers it.

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

    The reverse direction needs no guard *today*: a patient credential is
    not a Firebase or OIDC token, so the clinician dependencies reject it
    in their verifiers. That is a property of the credential format, not
    of this code, so it is written down as a requirement on resolver
    authors — see ``PatientPrincipalResolver``.

    **Not usable on a WebSocket route.** This takes a ``Request``, and
    ``DatabaseSessionMiddleware`` is a ``BaseHTTPMiddleware`` that does
    not run for the websocket scope at all — so neither the clinician
    short-circuit's stash nor the request-scoped session exists there. A
    patient WebSocket endpoint (companion chat is the obvious candidate)
    needs a parallel dependency that rebuilds *both* the guard and the
    arming; it must not reach for this one.

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

    if not _is_tenant_schema(context.practice_schema):
        # A resolver is trusted code, but this dependency is the single
        # choke point where a resolver's answer becomes a search_path, so
        # it fences it. ``_validate_schema_name`` further down only proves
        # the name is injection-safe: "platform", "public" and the shared
        # "practice" template all satisfy it, and so does any *other*
        # tenant's schema. A resolver that derived the schema from a token
        # claim and got it wrong would otherwise drive the session
        # straight into that schema with a patient principal armed.
        logger.error(
            "Patient resolver returned a non-tenant schema; refusing",
            extra={"credential_kind": credential.kind},
        )
        raise _unauthenticated()

    session = get_db_session()
    try:
        set_tenant_schema(session, context.practice_schema)
        arm_current_patient_id(session, context.patient_id)
    except ValueError:
        # ``set_tenant_schema`` and ``arm_current_patient_id`` raise
        # ValueError on a malformed schema or an empty patient id. Letting
        # that escape would be a 500 carrying the offending value, and a
        # 500 here is distinguishable from the uniform 401 — an oracle for
        # "the credential resolved but the principal was malformed" versus
        # "the credential did not resolve". Collapse it into the same 401
        # everything else gets.
        logger.error(
            "Patient resolver produced a malformed principal; refusing",
            extra={"credential_kind": credential.kind},
        )
        raise _unauthenticated() from None
    return context
