# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Converting between stored money and the money a person types or reads.

Money is stored as integer minor units (cents) throughout. Integers cannot
drift the way binary floats do, and it is the unit the payments provider takes
on the wire, so nothing has to be converted at that boundary.

The conversion that DOES have to happen is at the human boundary: a clinician
types ``160`` meaning $160, and reads ``$160``, while the column holds
``16000``. Getting that backwards is a hundred-fold error that does not
announce itself — the field still shows a plausible number, and it surfaces
later as a superbill for $1.60 or an invoice for $16,000.

So the conversion lives here, once, rather than at each screen that shows a
fee.

Never use ``float`` on the way in. ``160.10 * 100`` is ``16009.999...`` in
binary floating point, and ``int()`` of that is 16009 — a penny lost, silently,
on a value the user typed exactly. Every function here goes through
``Decimal``.

CURRENCY, deliberately: this module is USD-only and assumes 100 minor units to
the major unit. That assumption is invisible and wrong outside the dollar —
JPY has no minor unit at all (¥100 is 100 units, not 10,000), and KWD and BHD
have 1000. So going multi-currency is not "add a currency column"; it is
"``_CENTS`` becomes a per-currency exponent", plus a rule about what it means
to add two amounts.

Not built yet on purpose. Currency is the smallest part of serving a
non-US practice — NPI, DEA, CPT codes, state licensure, out-of-network
superbills and HIPAA all assume the United States, and none of them are
fixed by a currency column. What matters is that the conversion lives HERE
rather than on thirty screens, so the change stays a change to one module.
When it comes: currency belongs on the practice, not on each fee (a practice
bills in one currency), and it has to match the Stripe account's currency.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

#: What a caller may hand us as a dollar amount. ``float`` is accepted because
#: JSON gives us one, and is immediately routed through ``str`` so the decimal
#: the user typed is what gets converted.
DollarAmount = Decimal | int | float | str

_CENTS = Decimal(100)
_PENNY = Decimal("0.01")


def dollars_to_cents(amount: DollarAmount | None) -> int | None:
    """Convert a typed dollar amount to stored cents.

    ``None`` passes through as ``None`` — an unset fee is not a free one.

    Raises ``ValueError`` on anything that is not a number, so a stray empty
    string from a form becomes a 422 rather than a zero fee.
    """
    if amount is None:
        return None
    try:
        # str() first: Decimal(0.1) is 0.1000000000000000055511151231257827,
        # while Decimal("0.1") is exactly 0.1. The user typed the latter.
        exact = Decimal(str(amount))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a valid amount: {amount!r}") from exc
    if not exact.is_finite():
        raise ValueError(f"not a valid amount: {amount!r}")
    # Half-up rather than banker's rounding: a half-cent belongs to the invoice
    # the way a person expects it to, and the surprise of 0.005 rounding down
    # is not worth defending in a billing conversation.
    return int((exact * _CENTS).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def cents_to_dollars(cents: int | None) -> Decimal | None:
    """Convert stored cents to an exact dollar amount for editing.

    Returns a ``Decimal``, not a float, so the value can be rendered or
    re-submitted without picking up a rounding error on the way.
    """
    if cents is None:
        return None
    return (Decimal(cents) / _CENTS).quantize(_PENNY)


def format_money(cents: int | None, *, unset: str = "", free: str = "Free") -> str:
    """Render stored cents the way the interface shows them.

    Three states, deliberately distinguished:

    * ``None`` — nobody has set a fee. Renders as ``unset`` (empty by default),
      never as "$0", which would claim the visit is free.
    * ``0`` — the visit IS free, and says so.
    * anything else — ``$160`` for a whole amount, ``$160.50`` when there are
      cents. Trailing ``.00`` is noise on a fee list.
    """
    if cents is None:
        return unset
    if cents == 0:
        return free
    amount = (Decimal(cents) / _CENTS).quantize(_PENNY)
    whole = amount == amount.to_integral_value()
    return f"${amount:,.0f}" if whole else f"${amount:,.2f}"
