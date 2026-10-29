# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""One ``signed_in`` row in the user's practice audit log per sign-in.

Log-in monitoring (HIPAA § 164.308(a)(5)(ii)(C)) is recorded by the backend
itself, on the first authenticated request a sign-in makes, so it holds
whatever identity provider configuration a deployment runs.

A sign-in is identified by the token's subject and ``auth_time``. The identity
provider sets ``auth_time`` when the user actually authenticates and carries it
unchanged through every token refresh, so a refreshed token is the same sign-in
and writes nothing, while signing in again writes a new row. The row stores the
same opaque sign-in id the access log lines carry (``idle_session.session_log_key``),
which is how the next request finds it.

Three layers keep it to one row:

* a process-local memo, so steady-state requests cost nothing;
* a lookup in the practice audit log, so a restarted or second instance does
  not write the row again;
* a transaction-scoped advisory lock around that lookup and the write, so two
  first requests racing on different workers cannot both miss it.

Only the clinician's own token records a sign-in. The web app's server keeps a
session of its own for the pages it renders, and the token behind it is minted
from the browser's by a custom-token exchange: it has an ``auth_time`` of its
own (the exchange, and a fresh one each time the web app re-syncs its session)
and reaches this service from the web app's server, with that server's address
and agent. Recording it wrote a second row for every sign-in through the web
app, so it is recognised and skipped (``_minted_from_another_token``); the
browser's own requests carry the sign-in.

Best-effort: a failure is logged and the request carries on. A sign-in must not
be refused because the audit store hiccuped, and the next request tries again.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from ..db import get_db_session
from ..db.tenant_session import tenant_db_session
from ..models.audit import AuditAction
from ..repositories.postgres.audit import PostgresAuditRepository
from ..services.audit_service import AuditService
from ..services.session_generation_worker import resolve_tenant_schema_for_user
from . import idle_session

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)

# How far before ``auth_time`` the lookup reaches, for clock skew between the
# identity provider and this service. It only bounds the scan; the match is on
# the sign-in id.
_CLOCK_SKEW_MARGIN = timedelta(hours=1)

# A memo, not a record: losing it costs one lookup per sign-in, never a
# duplicate row. Cleared outright when full rather than evicted by age.
_MEMO_LIMIT = 10_000
_memo: set[str] = set()
_memo_lock = threading.Lock()


def _remembered(sign_in: str) -> bool:
    with _memo_lock:
        return sign_in in _memo


def _remember(sign_in: str) -> None:
    with _memo_lock:
        if len(_memo) >= _MEMO_LIMIT:
            _memo.clear()
        _memo.add(sign_in)


# The claim a custom-token exchange stamps on the token it mints from another:
# the provider the original token signed in with. The identity provider sets
# it only through a service-account-signed custom token, so a client cannot
# add it to its own sign-in to hide it. A passkey sign-in's custom token does
# not carry it; that token is the browser's own sign-in and is recorded.
_SOURCE_PROVIDER_CLAIM = "source_sign_in_provider"


def _minted_from_another_token(claims: dict[str, Any]) -> bool:
    """Whether this token is a copy of a sign-in, not the sign-in itself."""
    return _SOURCE_PROVIDER_CLAIM in claims


def record_sign_in(user_id: str, claims: dict[str, Any], request: Request | None) -> None:
    """Write this sign-in's ``signed_in`` row, unless it already has one.

    ``claims`` are the verified token's. ``request`` is the clinician's own
    request, so its address and agent are recorded. Never raises.
    """
    if _minted_from_another_token(claims):
        return
    sign_in = idle_session.session_log_key(claims)
    if sign_in is None or _remembered(sign_in):
        return

    try:
        get_db_session()
    except RuntimeError:
        # No database behind this request (the unit suite's in-memory fakes).
        return

    try:
        schema = resolve_tenant_schema_for_user(user_id)
        if schema is None:
            # No active practice to keep the record in yet, as for a new
            # account whose practice is still being provisioned. Not
            # remembered: the sign-in is recorded once the practice exists.
            logger.debug("sign_in_audit_no_practice user_id=%s", user_id)
            return

        since = datetime.fromtimestamp(claims["auth_time"], UTC) - _CLOCK_SKEW_MARGIN
        # A session of its own, pointed at the user's practice, so the
        # request's session keeps whatever schema it is on. It commits on
        # exit, which is what releases the advisory lock.
        with tenant_db_session(schema, user_id) as session:
            repo = PostgresAuditRepository(session)
            if not repo.lock_and_check_sign_in(user_id, sign_in, since):
                AuditService(repo).log_account_security_event(
                    AuditAction.SIGNED_IN, user_id, request, changes={"sign_in": sign_in}
                )
    except Exception:
        logger.warning("sign_in_audit_failed user_id=%s", user_id, exc_info=True)
        return
    _remember(sign_in)
