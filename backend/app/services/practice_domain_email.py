# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Email sending identities for a practice's domains: the extension point.

A practice that serves its portal from its own domain may want mail to come
from that domain too. Doing so means an identity at a mail provider for the
domain, and DKIM records the practice publishes for it. Which provider, and
how, is configurable per deployment, so the engine ships no provisioner: a
deployment that wants identities registers one at startup, before running the
domain reconciler job, and the job keeps each verified domain's identity in
step with its hosts.

With none registered the job leaves a domain's email fields empty, so no DKIM
records are shown.

No PHI: domain names and their identity state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..models.practice_domain import EmailIdentityStatus


@dataclass(frozen=True)
class EmailIdentity:
    status: EmailIdentityStatus
    #: Each token ``t`` is published as ``t._domainkey.<domain>`` CNAME
    #: ``t.<practice_domain_dkim_cname_suffix>``.
    dkim_tokens: tuple[str, ...] = ()


class EmailIdentityProvisioner(Protocol):
    def ensure(self, apex: str) -> EmailIdentity:
        """Create the domain's identity if missing, and report its state.

        Called each sweep for every domain whose ownership is confirmed and
        that still has a host, so it must be idempotent.
        """
        ...

    def remove(self, apex: str) -> None:
        """Delete the domain's identity. Called when its last host is gone;
        must succeed quietly when there is none."""
        ...


_provisioner_factory: Callable[[], EmailIdentityProvisioner] | None = None


def register_email_identity_provisioner(factory: Callable[[], EmailIdentityProvisioner]) -> None:
    """Supply what creates a domain's email identity. Called once at startup."""
    global _provisioner_factory  # noqa: PLW0603
    _provisioner_factory = factory


def email_identity_provisioner() -> EmailIdentityProvisioner | None:
    """The registered provisioner, or ``None`` when the deployment has none."""
    return _provisioner_factory() if _provisioner_factory is not None else None


def reset_email_identity_provisioner() -> None:
    """Drop the registration. For tests, so one case's provisioner does not
    leak into the next through the module-level slot."""
    global _provisioner_factory  # noqa: PLW0603
    _provisioner_factory = None
