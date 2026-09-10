# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Converting between stored money and the money a person types or reads.

Money is stored as integer cents: integers cannot drift the way floats do, and
cents are what the payments provider takes on the wire. The conversion that
does have to happen is at the human boundary, where ``160`` means $160 and the
column holds ``16000``. Getting that backwards is a silent hundred-fold error,
so the conversion lives here once rather than on every screen that shows a fee.

Never use ``float`` on the way in: ``160.10 * 100`` is ``16009.999...`` and
``int()`` of it loses a penny the user typed exactly. Everything here goes
through ``Decimal``.

USD-only, assuming 100 minor units per major unit. JPY has none and KWD has
1000, so multi-currency is "``_CENTS`` becomes a per-currency exponent", not
"add a currency column". Not built yet: currency is the smallest part of
serving a non-US practice. When it comes, it belongs on the practice and must
match the Stripe account's currency.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

#: ``float`` is accepted because JSON gives us one; it is routed through
#: ``str`` so the decimal the user typed is what gets converted.
DollarAmount = Decimal | int | float | str

_CENTS = Decimal(100)
_PENNY = Decimal("0.01")


def dollars_to_cents(amount: DollarAmount | None) -> int | None:
    """Convert a typed dollar amount to stored cents.

    ``None`` passes through: an unset fee is not a free one. Raises
    ``ValueError`` on anything that is not a number, so a stray empty string
    from a form becomes a 422 rather than a zero fee.
    """
    if amount is None:
        return None
    try:
        # str() first: Decimal(0.1) is not 0.1, but Decimal("0.1") is.
        exact = Decimal(str(amount))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a valid amount: {amount!r}") from exc
    if not exact.is_finite():
        raise ValueError(f"not a valid amount: {amount!r}")
    # Half-up: a half-cent rounds the way a person expects on an invoice.
    return int((exact * _CENTS).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def cents_to_dollars(cents: int | None) -> Decimal | None:
    """Convert stored cents to an exact ``Decimal`` for editing or re-submission."""
    if cents is None:
        return None
    return (Decimal(cents) / _CENTS).quantize(_PENNY)


def format_money(cents: int | None, *, unset: str = "", free: str = "Free") -> str:
    """Render stored cents the way the interface shows them.

    ``None`` renders as ``unset`` (never "$0", which would claim the visit is
    free); ``0`` renders as ``free``; anything else as ``$160`` or ``$160.50``.
    """
    if cents is None:
        return unset
    if cents == 0:
        return free
    amount = (Decimal(cents) / _CENTS).quantize(_PENNY)
    whole = amount == amount.to_integral_value()
    return f"${amount:,.0f}" if whole else f"${amount:,.2f}"
