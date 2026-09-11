# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Handing a held remittance to whoever is going to look into it.

A hold says the engine refused to guess. It does not say *why* the payer's
two statements disagreed, and answering that is not the engine's job — it
needs somebody to read the remittance, compare it against what the parser
believed, and reach a conclusion.

Different deployments will answer that differently. Some will want the
remittance kept for later study, some will want it raised to whoever runs
the deployment, some will want nothing beyond what the practice already
sees. So the engine states an interface and asks for it; a deployment
registers an implementation at start-up, and the engine never imports the
implementation. Same shape as
:mod:`app.claims.credentials`.

**The default is a no-op, and that is the ordinary mode rather than a
degraded one.** A deployment that registers nothing behaves exactly as it
does today: the practice still meets the hold, and can still settle it.

**The engine's behaviour never depends on the answer.** A receiver that
raises, hangs or is absent must not change what the practice sees, must not
release the hold, and must not stop the posting path. Every call is wrapped
and every failure is swallowed with a log line — which is the opposite of
the usual advice, and is right here because the receiver is an observer of
a decision that has already been made safely. If it breaks, the client is
still not billed and the practice is still asked; only the investigation
is delayed.

**The engine keeps no second copy of the remittance.** What crosses the
seam is the parsed remittance the posting path already had in hand, at the
moment it had it. Retention beyond what the engine already stores is the
receiver's business, and the receiver is code running inside the same
deployment boundary — so nothing here delegates a PHI decision. What a
receiver then does with what it is handed is the receiver's
responsibility, and an implementor should read that sentence twice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from ..models.claims_holds import RemittanceHold
    from ..models.claims_responses import RemittanceClaim

logger = logging.getLogger(__name__)

#: What happened to the hold. ``raised`` carries the remittance behind it;
#: the rest are transitions, where the engine has no document in hand.
#:
#: Transitions cross the seam too, not only creation, so a receiver can
#: follow a hold to its conclusion without polling for it.
HoldTransition = Literal["raised", "acknowledged", "resolved"]


@dataclass(frozen=True, slots=True)
class HeldRemittance:
    """A hold, and enough to find the document behind it again.

    ``remittance`` is what the parser believed at the moment it refused to
    bill — the other half of the comparison a person doing the
    investigation has to make, and the half that is nowhere else. It is
    ``None`` on a transition, where the engine is reporting a change of
    state and has no document open.

    ``vendor_transaction_id`` is the clearinghouse transaction the
    remittance arrived on, which is how the original file is found in the
    vendor's own records. It is ``None`` when the adjudication was read
    from a claim timeline rather than from a delivered 835.
    """

    hold: RemittanceHold
    transition: HoldTransition
    remittance: RemittanceClaim | None = None
    vendor_transaction_id: str | None = None


class RemittanceHoldReceiver(Protocol):
    """Whatever a deployment wants done about a held remittance."""

    def receive(self, held: HeldRemittance) -> None:
        """Called when a hold is raised, and again on each transition.

        May do anything, including nothing, and may raise: the caller
        swallows it. It must not assume it will be called again, and must
        not assume it was called at all for any earlier transition — the
        engine makes a best effort, not a delivery guarantee. A receiver
        that needs completeness reads the table.
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

#: The vendor transaction id is embedded in the posting key the webhook
#: path builds: ``{claim_id}:835:{transaction_id}:{control_number}``. The
#: timeline path's key has no ``835:`` segment, and correctly yields
#: ``None`` — that adjudication was read from an API rather than delivered
#: as a document, so there is no transaction to go and find.
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

    The swallow is the point. This is called from the posting path, which
    has just refused to bill a client, and from the route a therapist used
    to settle one — neither of which may fail because an observer did.
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
