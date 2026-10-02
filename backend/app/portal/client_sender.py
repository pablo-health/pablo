# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Who a practice's email to its clients is from, and where replies go.

Three settings, practice-wide (see
:class:`~app.db.platform_models.PracticeEmailSenderRow`): the sender name
(defaults to the practice's name), the mailbox name on the practice's own
domain (defaults to ``portal``), and the reply-to address, which has **no
default**.

**The rule, in** :func:`resolve_client_sender`:

* When the practice holds a domain whose email sending identity is verified
  AND has saved a reply-to address, mail leaves from
  ``<mailbox>@<that domain>`` under the sender name, with that Reply-To.
* Otherwise it leaves from the deployment's own address, still under the
  sender name. A client never gets a less trustworthy email while the
  practice's domain is being set up; it changes over on its own once both are
  in place.

**Why the reply-to has no default.** Mail from the practice's own domain
invites replies, and those must reach an address the practice chose for them.
Falling back to anybody's sign-in address would send clients' replies to a
personal inbox the practice never offered — the owner may have signed up with
one. Until a reply-to is saved, nothing names one, and replies follow the
deployment's own From.

With several verified domains, the one the practice's primary portal host
sits under wins, and otherwise the oldest; nothing to configure, and the same
answer every time.

**Never a person's own mailbox.** The point of a separate mailbox name is that
automated mail, bounces and auto-replies stay out of anybody's real inbox. So a
mailbox name that would make the From address someone's sign-in address, or
the reply-to address, is refused when it is saved, and if a domain added later
makes it so, the send falls back to the deployment's address instead.

How a channel puts this on a message is the channel's business: see
:class:`~app.portal.delivery.PracticeSenderDelivery`.

No PHI: a practice's name for itself, a mailbox name, domains and a staff
address. Client addresses never pass through here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import parseaddr
from typing import TYPE_CHECKING, Protocol

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select

from ..db import create_standalone_session
from ..db.platform_models import (
    EmailTenantMappingRow,
    PracticeDomainApexRow,
    PracticeDomainRow,
    PracticeEmailSenderRow,
    PracticeRow,
)
from ..settings import get_settings
from ..utcnow import utc_now
from .delivery import ClientSender

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

#: The mailbox name on the practice's own domain when it has chosen none.
DEFAULT_LOCAL_PART = "portal"

SENDER_NAME_MAX = 100
REPLY_TO_MAX = 254
_LOCAL_PART_MAX = 64

#: Letters, digits, and ``. _ + -`` between them: every address a person would
#: type, and none a mail system would read two ways.
_LOCAL_PART = re.compile(r"^[a-z0-9](?:[a-z0-9._+-]*[a-z0-9])?$")
#: Mailbox names that say nobody reads replies. Replies go to the reply-to
#: address, so there is no need for one, and clients are less likely to trust it.
_NO_REPLY = re.compile(r"^(?:no|do[._-]?not)[._-]?reply$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class SenderSettingsError(ValueError):
    """A setting that cannot be saved. The message says what to change."""


@dataclass(frozen=True)
class SenderSettings:
    """What a practice has chosen. ``None`` is the default for that field."""

    sender_name: str | None = None
    sender_local_part: str | None = None
    reply_to: str | None = None


@dataclass(frozen=True)
class SenderDefaults:
    """The defaults for the two fields that have one. The reply-to has none."""

    sender_name: str
    sender_local_part: str


def clean_sender_name(raw: str | None) -> str | None:
    """*raw* trimmed, ``None`` for blank. Raises for one that cannot be a name."""
    name = (raw or "").strip()
    if not name:
        return None
    if _CONTROL.search(name):
        raise SenderSettingsError("Use a name on one line.")
    if len(name) > SENDER_NAME_MAX:
        raise SenderSettingsError(f"Use a name of {SENDER_NAME_MAX} characters or fewer.")
    return name


def clean_local_part(raw: str | None) -> str | None:
    """*raw* as a mailbox name (lowercased), ``None`` for blank."""
    local = (raw or "").strip().lower()
    if not local:
        return None
    if "@" in local:
        raise SenderSettingsError("Enter only the part before the @, like portal or hello.")
    if len(local) > _LOCAL_PART_MAX or not _LOCAL_PART.match(local) or ".." in local:
        raise SenderSettingsError(
            "Use letters, numbers, and . _ + - between them, like portal or hello."
        )
    if _NO_REPLY.match(local):
        raise SenderSettingsError(
            "Clients can reply to these emails, so pick a name that invites it, "
            "like portal or hello."
        )
    return local


def clean_reply_to(raw: str | None) -> str | None:
    """*raw* as an email address, ``None`` for blank. Raises for one that is not."""
    address = (raw or "").strip()
    if not address:
        return None
    if len(address) > REPLY_TO_MAX:
        raise SenderSettingsError("Enter a shorter email address.")
    try:
        # Not globally_deliverable: a self-hosted practice may route replies to
        # a mail server on an internal name.
        validated = validate_email(address, check_deliverability=False, globally_deliverable=False)
    except EmailNotValidError:
        raise SenderSettingsError("Enter an email address, like frontdesk@example.com.") from None
    return validated.normalized


def defaults_for(practice: PracticeRow) -> SenderDefaults:
    return SenderDefaults(
        sender_name=practice.name,
        sender_local_part=DEFAULT_LOCAL_PART,
    )


def _verified_apexes(session: Session, practice_id: str) -> list[str]:
    """The practice's domains that can send email, oldest first."""
    stmt = (
        select(PracticeDomainApexRow.apex)
        .where(
            PracticeDomainApexRow.practice_id == practice_id,
            PracticeDomainApexRow.email_identity_status == "verified",
        )
        .order_by(PracticeDomainApexRow.created_at, PracticeDomainApexRow.apex)
    )
    return list(session.execute(stmt).scalars())


def _primary_portal_host(session: Session, practice_id: str) -> str | None:
    # Primary whatever its status: a primary whose certificate has lapsed is
    # still the practice's choice of name, and its domain's email is unaffected.
    stmt = select(PracticeDomainRow.domain).where(
        PracticeDomainRow.practice_id == practice_id,
        PracticeDomainRow.purpose == "portal",
        PracticeDomainRow.is_primary.is_(True),
    )
    return session.execute(stmt).scalar_one_or_none()


def sending_domain(session: Session, practice_id: str) -> str | None:
    """The domain the practice's client email leaves from, or ``None`` for none.

    The verified domain the primary portal host sits under, else the oldest
    verified domain.
    """
    verified = _verified_apexes(session, practice_id)
    if not verified:
        return None
    primary = _primary_portal_host(session, practice_id)
    if primary is not None:
        for apex in verified:
            if primary == apex or primary.endswith(f".{apex}"):
                return apex
    return verified[0]


def _personal_addresses(session: Session, practice_id: str) -> set[str]:
    """Addresses people of this practice sign in with, lowercased."""
    stmt = select(func.lower(EmailTenantMappingRow.email)).where(
        EmailTenantMappingRow.practice_id == practice_id
    )
    return set(session.execute(stmt).scalars())


def _load(session: Session, practice_id: str) -> tuple[PracticeRow, SenderSettings]:
    practice = session.get(PracticeRow, practice_id)
    if practice is None:
        raise LookupError("no such practice")
    row = session.get(PracticeEmailSenderRow, practice_id)
    chosen = (
        SenderSettings()
        if row is None
        else SenderSettings(
            sender_name=row.sender_name,
            sender_local_part=row.sender_local_part,
            reply_to=row.reply_to,
        )
    )
    return practice, chosen


def _resolve(session: Session, practice: PracticeRow, chosen: SenderSettings) -> ClientSender:
    defaults = defaults_for(practice)
    reply_to = chosen.reply_to
    local = chosen.sender_local_part or defaults.sender_local_part
    # The practice's own domain only once replies have somewhere the practice
    # chose to go; see the module docstring.
    apex = sending_domain(session, practice.id) if reply_to else None
    from_address = f"{local}@{apex}" if apex is not None else None
    if from_address is not None:
        taken = _personal_addresses(session, practice.id)
        if reply_to:
            taken.add(reply_to.lower())
        if from_address in taken:
            from_address = None
    return ClientSender(
        from_name=chosen.sender_name or defaults.sender_name,
        from_address=from_address,
        reply_to=reply_to,
    )


def resolve_client_sender(practice_id: str) -> ClientSender:
    """Who the practice's next email to a client is from. See the module docstring.

    Opens and closes its own session, and is asked once per send, so a domain
    that verifies takes effect on the next email. Raises ``LookupError`` for a
    practice that does not exist.
    """
    session = create_standalone_session()
    try:
        practice, chosen = _load(session, practice_id)
        return _resolve(session, practice, chosen)
    finally:
        session.close()


def resolve_client_sender_for_schema(schema: str) -> ClientSender | None:
    """:func:`resolve_client_sender` for the practice living in *schema*.

    For callers that know a tenant schema rather than a practice id. ``None``
    when no practice lives there.
    """
    session = create_standalone_session()
    try:
        practice_id = session.execute(
            select(PracticeRow.id).where(PracticeRow.schema_name == schema)
        ).scalar_one_or_none()
        if practice_id is None:
            return None
        practice, chosen = _load(session, practice_id)
        return _resolve(session, practice, chosen)
    finally:
        session.close()


@dataclass(frozen=True)
class SenderView:
    """Everything the settings screen shows."""

    chosen: SenderSettings
    defaults: SenderDefaults
    #: The domain mail leaves from now, or ``None`` while none can send.
    sending_domain: str | None
    #: What the next email will carry.
    effective: ClientSender


class SenderSettingsStore(Protocol):
    def view(self, practice_id: str) -> SenderView:
        """The practice's settings, their defaults, and what they come to."""

    def save(self, practice_id: str, chosen: SenderSettings, *, by: str) -> SenderView:
        """Keep *chosen* (already cleaned). Raises :class:`SenderSettingsError`."""


class PlatformSenderSettingsStore:
    def view(self, practice_id: str) -> SenderView:
        session = create_standalone_session()
        try:
            practice, chosen = _load(session, practice_id)
            return SenderView(
                chosen=chosen,
                defaults=defaults_for(practice),
                sending_domain=sending_domain(session, practice_id),
                effective=_resolve(session, practice, chosen),
            )
        finally:
            session.close()

    def save(self, practice_id: str, chosen: SenderSettings, *, by: str) -> SenderView:
        session = create_standalone_session()
        try:
            practice, _ = _load(session, practice_id)
            _refuse_personal_mailbox(session, practice, chosen)
            row = session.get(PracticeEmailSenderRow, practice_id)
            if row is None:
                row = PracticeEmailSenderRow(practice_id=practice_id)
                session.add(row)
            row.sender_name = chosen.sender_name
            row.sender_local_part = chosen.sender_local_part
            row.reply_to = chosen.reply_to
            row.updated_at = utc_now()
            row.updated_by = by
            session.commit()
        finally:
            session.close()
        return self.view(practice_id)


def _refuse_personal_mailbox(
    session: Session, practice: PracticeRow, chosen: SenderSettings
) -> None:
    """Refuse a mailbox name that is somebody's address on one of the practice's domains.

    Checked against every domain the practice holds, verified or not, so a
    name that is fine today does not become a person's mailbox the day a
    domain verifies.
    """
    local = chosen.sender_local_part or DEFAULT_LOCAL_PART
    taken = _personal_addresses(session, practice.id)
    if chosen.reply_to:
        taken.add(chosen.reply_to.lower())
    apexes = session.execute(
        select(PracticeDomainApexRow.apex).where(PracticeDomainApexRow.practice_id == practice.id)
    ).scalars()
    for apex in apexes:
        if f"{local}@{apex}" in taken:
            raise SenderSettingsError(
                f"{local}@{apex} is someone's own address. Use a separate name, "
                "like portal or hello, so automated mail stays out of personal inboxes."
            )


def get_sender_settings_store() -> SenderSettingsStore:
    return PlatformSenderSettingsStore()


# ── the deployment's own address ──────────────────────────────────────────

_deployment_from_address: Callable[[], str | None] | None = None


def register_deployment_from_address(resolver: Callable[[], str | None]) -> None:
    """Say which address client email leaves from when the practice's domain cannot send.

    For the settings screen's preview only; sending does not ask. A deployment
    whose email channel is not the engine's SMTP sender registers this at
    startup. Answer ``None`` when the address is not known here.
    """
    global _deployment_from_address  # noqa: PLW0603
    _deployment_from_address = resolver


def reset_deployment_from_address() -> None:
    """Drop the registration. For tests."""
    global _deployment_from_address  # noqa: PLW0603
    _deployment_from_address = None


def deployment_from_address() -> str | None:
    """The deployment's own sending address, without a display name, if known."""
    if _deployment_from_address is not None:
        return _deployment_from_address()
    settings = get_settings()
    if settings.email_backend != "smtp":
        return None
    return parseaddr(settings.smtp_from)[1] or None
