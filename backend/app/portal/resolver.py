# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The portal front door on the patient-principal seam.

:mod:`app.auth.patient_context` defines a patient principal and a registry of
resolvers keyed by credential kind. This is the resolver that fills it: it
turns a portal session token into a ``PatientContext``, so every
patient-facing route — intake, secure messaging, appointments, self-booking —
authenticates against one seam rather than each knowing about magic links.

Two halves, and the second is the one a stateless verifier would miss:

1. The signature, type and expiry, from
   :func:`app.portal.tokens.verify_session_token`.
2. The session ROW. A token whose row is missing, revoked or past its expiry
   resolves to ``None`` — which is what makes a clinician's kill switch
   immediate instead of a promise that expires within the hour.

**Rejection is ``None``, never an exception.** Per the protocol's contract, a
resolver that raises aborts resolution for the whole request, so every
resolver registered behind it is skipped. Raising on a forged signature would
therefore deny credentials a later front door would legitimately have
accepted, and — once a second front door exists — anyone able to make this
one raise could pick which door answers. So everything here funnels into
``None``.

Nothing is logged with a patient id, a practice, or any part of a token. A
resolution failure is not an event worth a line of its own; the dependency's
uniform 401 is the record.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from ..auth.patient_context import (
    AuthStrength,
    PatientContext,
    PatientCredential,
    patient_resolver_registry,
)
from ..db import DEFAULT_PRACTICE_SCHEMA, create_standalone_session
from ..settings import get_settings
from . import tokens
from .db_store import DbPortalSessionStore

if TYPE_CHECKING:
    from .store import PortalSessionRecord

logger = logging.getLogger(__name__)

#: The registry key. ``bearer`` is the ``Authorization`` scheme the
#: credential arrives under; ``credential_kind`` on the returned context is
#: the narrower name below, so an audit row or a later policy can tell a
#: portal session from whatever else registers under ``bearer``.
CREDENTIAL_KIND = "bearer"
PORTAL_SESSION_KIND = "portal_session"


def _now() -> int:
    """Wall clock in unix seconds, in one place so tests can patch it."""
    return int(time.time())


class PortalSessionResolver:
    """Resolves a portal session token to the patient who holds it."""

    credential_kind = CREDENTIAL_KIND

    def _signing_key(self) -> str:
        # Read per-resolve rather than captured at registration: this is
        # registered once at startup, and a key captured then would outlive
        # a rotation.
        return get_settings().portal_token_signing_key.get_secret_value()

    def _verified_claims(self, raw_token: str) -> tokens.SessionClaims | None:
        """Claims from a token that survives every stateless check.

        A clinician's identity-provider JWT arrives under the same
        ``bearer`` scheme and lands here too. It is refused by the signature
        check — the provider signs it RS256 against its own keys, and this
        verifier accepts only HS256 under the deployment's own key — so the
        separation the protocol demands is structural, not a matter of this
        code remembering to look.
        """
        key = self._signing_key()
        if not key:
            # An empty key would check every signature against nothing.
            # Fail closed rather than verify vacuously.
            return None
        try:
            claims = tokens.verify_session_token(signing_key=key, token=raw_token)
        except Exception:
            # Wrong type, wrong key, expired, malformed, or a clinician
            # token — all of them "not my credential".
            return None
        if not claims.tenant.startswith(f"{DEFAULT_PRACTICE_SCHEMA}_"):
            # The shared template, ``platform`` and ``public`` are all valid
            # identifiers and all wrong answers. ``get_patient_context``
            # checks this too; a resolver handing back a bad schema is the
            # thing that check exists to catch, so don't be that resolver.
            return None
        return claims

    def _live_record(self, claims: tokens.SessionClaims) -> PortalSessionRecord | None:
        """The session's row, if it still authorizes anything.

        Missing, revoked or expired all answer ``None`` — this is the half a
        stateless verifier cannot do, and what makes a clinician's revoke
        immediate rather than a promise that expires within the hour.
        """
        try:
            session = create_standalone_session(claims.tenant)
        except ValueError:
            return None
        try:
            record = DbPortalSessionStore(session).get(claims.jti)
        except Exception:
            # A database failure genuinely is "could not decide" — but the
            # protocol's escape hatch for that (raising) aborts resolution
            # for every front door behind this one. Refusing this single
            # credential is the smaller blast radius, and the caller gets
            # the same uniform 401 either way.
            logger.warning("Portal session lookup failed; refusing the credential")
            return None
        finally:
            session.close()
        if record is None or not record.is_live(now=_now()):
            return None
        return record

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        """A live portal session becomes its patient principal, else ``None``."""
        claims = self._verified_claims(credential.value)
        if claims is None:
            return None
        record = self._live_record(claims)
        if record is None:
            return None
        if record.patient_id != claims.patient_id:
            # Signature good, row present, and the two disagree about who
            # this is. Nothing legitimate produces that.
            return None

        return PatientContext(
            patient_id=record.patient_id,
            practice_schema=claims.tenant,
            credential_kind=PORTAL_SESSION_KIND,
            # A session only exists because both factors were cleared at
            # redemption — link possession plus the texted code — and
            # refresh rotates that proof forward rather than weakening it.
            auth_strength=AuthStrength.STEPPED_UP,
        )


def register_portal_resolver() -> None:
    """Register the front door on the process-wide registry.

    Called once at startup, before the first request. The registry is
    otherwise empty, so without this a deployment has patient-facing routes
    and nothing that can sign a patient into them.
    """
    patient_resolver_registry.register(PortalSessionResolver())
    logger.info("Registered the portal patient-principal resolver")
