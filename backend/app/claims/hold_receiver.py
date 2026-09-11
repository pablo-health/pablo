# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Handing a held remittance to whoever will investigate it.

A hold says the engine refused to guess. Working out WHY the payer's two
statements disagreed is not the engine's job, and different deployments will
answer it differently — so the engine states an interface and a deployment
registers an implementation, the same shape as
:mod:`app.claims.credentials`.

The default is a no-op, and that is the ordinary mode rather than a degraded
one: the practice still meets the hold and can still settle it.

The engine's behaviour never depends on the answer. Every call is wrapped
and every failure swallowed with a log line — the opposite of the usual
advice, and right here because the safe decision has already been made and
written down by the time this runs. A broken receiver delays the
investigation and nothing else.

The engine keeps no second copy of the remittance. What crosses is what the
posting path already had in hand. A receiver runs inside the same deployment
boundary, so nothing here delegates a PHI decision — what it then does with
what it is handed is its own responsibility.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from ..models.claims_holds import RemittanceHold
    from ..models.claims_responses import RemittanceClaim

logger = logging.getLogger(__name__)

#: ``raised`` carries the remittance; the transitions do not. They cross
#: too, so a receiver can follow a hold to its end without polling.
HoldTransition = Literal["raised", "acknowledged", "resolved"]


@dataclass(frozen=True, slots=True)
class HeldRemittance:
    """A hold, and enough to find the document behind it again.

    ``remittance`` is what the parser believed — the half of the comparison
    that exists nowhere else. ``None`` on a transition.

    ``vendor_transaction_id`` finds the original file in the vendor's
    records, and is ``None`` when the adjudication came from a claim
    timeline rather than a delivered 835.
    """

    hold: RemittanceHold
    transition: HoldTransition
    remittance: RemittanceClaim | None = None
    vendor_transaction_id: str | None = None


class RemittanceHoldReceiver(Protocol):
    """Whatever a deployment wants done about a held remittance."""

    def receive(self, held: HeldRemittance) -> None:
        """Called when a hold is raised, and again on each transition.

        May raise; the caller swallows it. Best effort, not a delivery
        guarantee — a receiver needing completeness reads the table.
        """
        ...


class NoopRemittanceHoldReceiver:
    """The default. Does nothing, deliberately."""

    def receive(
        self,
        held: HeldRemittance,  # noqa: ARG002 — argument documents the protocol's shape
    ) -> None:
        return


@dataclass
class _Registry:
    receiver: RemittanceHoldReceiver | None = None


_registry = _Registry()
_default_receiver = NoopRemittanceHoldReceiver()

#: The webhook path's posting key is
#: ``{claim_id}:835:{transaction_id}:{control_number}``. A timeline key has
#: no ``835:`` segment and correctly yields ``None`` — no document to find.
_WEBHOOK_KEY_MARKER = ":835:"


def vendor_transaction_of(posting_key: str) -> str | None:
    """The clearinghouse transaction a hold's remittance arrived on, if any."""
    marker = posting_key.find(_WEBHOOK_KEY_MARKER)
    if marker == -1:
        return None
    rest = posting_key[marker + len(_WEBHOOK_KEY_MARKER) :]
    transaction_id, _, _control_number = rest.partition(":")
    return transaction_id or None


def register_remittance_hold_receiver(receiver: RemittanceHoldReceiver | None) -> None:
    """Install the process-global receiver, or ``None`` to restore the default.

    Call once during startup, before the first request.
    """
    _registry.receiver = receiver


def get_remittance_hold_receiver() -> RemittanceHoldReceiver:
    """The registered receiver, or :class:`NoopRemittanceHoldReceiver`."""
    return _registry.receiver or _default_receiver


def hand_over(
    hold: RemittanceHold,
    transition: HoldTransition,
    *,
    remittance: RemittanceClaim | None = None,
) -> None:
    """Offer the hold to whatever the deployment registered. Never raises.

    Called from the posting path and from the settle route; neither may fail
    because an observer did.
    """
    receiver = get_remittance_hold_receiver()
    try:
        receiver.receive(
            HeldRemittance(
                hold=hold,
                transition=transition,
                remittance=remittance,
                vendor_transaction_id=vendor_transaction_of(hold.posting_key),
            )
        )
    except Exception as exc:  # a receiver must never take the caller down
        logger.warning(
            "remittance_hold_receiver_failed receiver=%s error=%s hold_id=%s transition=%s",
            type(receiver).__qualname__,
            type(exc).__name__,
            hold.id,
            transition,
        )
