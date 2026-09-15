# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The authorisation that lets Pablo apply to panels on a clinician's behalf.

Pablo runs the applications, and running one means signing her name to a form
and ringing a payer to chase it. Neither is ours to do without being asked in
writing, so this is the writing.

Shaped after the BAA — versioned documents on disk, accepted in the product,
the text kept beside the acceptance. The version discovery here is deliberately
the same as ``auth.service.get_baa_version``, including its most useful
property: **no bundled document means no flow**. A self-hosted deployment has
no Pablo staff to authorise, so the absence of the file is the correct answer
rather than a misconfiguration, and every caller here treats it that way.

The documents themselves therefore live in the managed deployment's overlay,
not in this repository — the same split the BAA already uses.

There are TWO of them, and they authorise different things. The services
agreement is the commercial relationship; the credentialing authorisation is
the narrow permission to sign her name to a payer's form and to speak to that
payer as her. Each is revised on its own schedule, so each carries its own
version series rather than sharing one — re-wording the commercial terms should
not invalidate a signature about signing authority, or the reverse.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select

from ..db.platform_models import PayerAuthorizationRow

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

#: Where the dated documents live when a deployment bundles them. Sibling of
#: ``backend/baa``, and absent in this repository on purpose.
AUTHORIZATION_DIR = (Path(__file__).parent.parent.parent / "payer_authorizations").resolve()

#: The narrow permission to sign her name to a payer's form and to chase it
#: with that payer. This is the one the panel work gates on.
CREDENTIALING_AUTHORIZATION = "credentialing_authorization"

#: The commercial relationship underneath it.
SERVICES_AGREEMENT = "services_agreement"

#: Every kind a row may carry. The database holds the same list as a check
#: constraint; this is the readable copy, and the two are meant to agree.
AUTHORIZATION_KINDS: tuple[str, ...] = (CREDENTIALING_AUTHORIZATION, SERVICES_AGREEMENT)

_VERSION_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Each kind is discovered by its own filename prefix, so one directory can
#: hold both series without either shadowing the other.
_FILENAME_PREFIXES: dict[str, str] = {
    CREDENTIALING_AUTHORIZATION: "PAYER-AUTH-",
    SERVICES_AGREEMENT: "SERVICES-AGREEMENT-",
}


def available_versions(kind: str = CREDENTIALING_AUTHORIZATION) -> dict[str, Path]:
    """Every bundled version of one kind, newest first, as ``version -> path``."""
    prefix = _FILENAME_PREFIXES.get(kind)
    if prefix is None or not AUTHORIZATION_DIR.is_dir():
        return {}
    found = {}
    for path in sorted(AUTHORIZATION_DIR.glob(f"{prefix}*.md"), reverse=True):
        version = path.stem.removeprefix(prefix)
        if _VERSION_PATTERN.match(version):
            found[version] = path.resolve()
    return found


def current_version(kind: str = CREDENTIALING_AUTHORIZATION) -> str:
    """The version in force, or ``""`` when no document of that kind is bundled.

    Empty is a real answer, not a failure: a deployment with no Pablo staff
    behind it has nobody to authorise, so the flow is off rather than broken.
    It is also the answer for a kind whose text counsel has not settled yet,
    which is the same shape of "not available" and wants the same handling.
    """
    versions = available_versions(kind)
    return next(iter(versions), "")


def read_version(version: str, kind: str = CREDENTIALING_AUTHORIZATION) -> str | None:
    """The text of one version, or ``None`` if this deployment has no such file.

    Reads through :func:`available_versions` rather than joining the version
    onto a path, so a version string can never walk out of the directory.
    """
    path = available_versions(kind).get(version)
    return path.read_text() if path is not None else None


def signatures_for(
    session: Session,
    user_id: str,
    kind: str | None = None,
) -> list[PayerAuthorizationRow]:
    """Every signature she has given, newest first.

    A list rather than the latest one, because the history is the record: the
    question a payer asks is what authority we held on a date, not what we hold
    now.

    ``kind`` narrows to one document; omitted, it returns both series
    interleaved by date, which is what an "everything she has signed" view
    wants.
    """
    query = select(PayerAuthorizationRow).where(PayerAuthorizationRow.user_id == user_id)
    if kind is not None:
        query = query.where(PayerAuthorizationRow.kind == kind)
    return list(session.scalars(query.order_by(PayerAuthorizationRow.signed_at.desc())).all())


def in_force(
    session: Session,
    user_id: str,
    kind: str = CREDENTIALING_AUTHORIZATION,
) -> PayerAuthorizationRow | None:
    """Her live signature of the CURRENT version of one kind, if she has one.

    Three things have to hold, and each rules out a different way of being
    wrong: she signed, she has not withdrawn it, and what she signed is what we
    would be acting under today. A signature of a superseded version is real
    history and no longer permission — which is why this asks for the current
    version rather than merely for the newest row.

    Asked about one kind, never about "any": the two documents authorise
    different things, so a signed services agreement must not read as
    permission to sign her name to a payer's form.

    ``None`` when the deployment bundles no document at all. That is the same
    answer as "she has not signed", and it is the right one: the gate this
    feeds should refuse either way rather than wave work through because a file
    is missing.
    """
    version = current_version(kind)
    if not version:
        return None
    return session.scalars(
        select(PayerAuthorizationRow)
        .where(
            PayerAuthorizationRow.user_id == user_id,
            PayerAuthorizationRow.kind == kind,
            PayerAuthorizationRow.version == version,
            PayerAuthorizationRow.revoked_at.is_(None),
        )
        .order_by(PayerAuthorizationRow.signed_at.desc())
    ).first()


def sign(  # noqa: PLR0913 — service deps + keyword-only signature fields
    session: Session,
    user_id: str,
    *,
    version: str,
    signed_name: str,
    at: datetime,
    kind: str = CREDENTIALING_AUTHORIZATION,
) -> PayerAuthorizationRow:
    """Record her signature, with the text she was shown.

    Flushed, not committed — the caller owns the transaction.

    Raises :class:`UnknownVersionError` if the version is not bundled for this
    kind. A signature against text we cannot produce is not a record of
    anything, and a version that exists under the other document is not this
    one.
    """
    text = read_version(version, kind)
    if text is None:
        raise UnknownVersionError(version, kind)
    row = PayerAuthorizationRow(
        id=str(uuid.uuid4()),
        user_id=user_id,
        kind=kind,
        version=version,
        full_text=text,
        signed_name=signed_name,
        signed_at=at,
        created_at=at,
        updated_at=at,
    )
    session.add(row)
    session.flush()
    return row


def revoke(session: Session, user_id: str, *, at: datetime) -> int:
    """Withdraw every live signature she has. Returns how many were withdrawn.

    Withdrawal only stamps a timestamp. What she signed, and that she signed
    it, both stay true — a record that erased itself on withdrawal could not
    answer for the period when the authority did hold.

    Sweeps every unrevoked row rather than only the current version, and every
    kind rather than only the credentialing authorisation: she is saying "stop
    acting for me", and leaving a superseded signature — or the agreement the
    authority sits on — standing would be reading that as narrowly as possible.
    """
    live = session.scalars(
        select(PayerAuthorizationRow).where(
            PayerAuthorizationRow.user_id == user_id,
            PayerAuthorizationRow.revoked_at.is_(None),
        )
    ).all()
    for row in live:
        row.revoked_at = at
        row.updated_at = at
    session.flush()
    return len(live)


class UnknownVersionError(ValueError):
    """A version this deployment does not bundle for that kind."""

    def __init__(self, version: str, kind: str = CREDENTIALING_AUTHORIZATION) -> None:
        super().__init__(f"No {kind} version {version!r} is available.")
        self.version = version
        self.kind = kind
