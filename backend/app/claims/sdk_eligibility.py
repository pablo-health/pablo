# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading the vendor SDK's eligibility answer.

The older adapter asks the vendor's compatibility endpoint and gets a 271
transcribed into JSON: a flat list of benefit lines, each tagged with an X12
``EB01`` code that ``app.claims.eligibility`` decodes itself — ``B`` is a
copayment, ``C`` a deductible, ``F`` a limitation, ``U`` "ask somebody else".

The vendor's own eligibility API has already done that sorting. Benefits
arrive under named keys, network and time period arrive as words rather than
codes, and the payer that administers a carved-out benefit arrives under
``other_or_additional_payer``, which the vendor documents as the
coordination-of-benefits signal. So this module is a mapping and not a
decoder, and the code that used to do the decoding does not run on this path
at all.

What is deliberately *not* read here: the many benefit kinds the chart has no
question about (spend-down, reserve, prior-years history, and the rest). The
whole payer response is kept verbatim in ``EligibilityOutcome.stored`` for
anyone who needs to go back to it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..models.eligibility import (
    AaaError,
    CarveoutAdministrator,
    EligibilityOutcome,
    EligibilityStatus,
    VisitLimit,
)

if TYPE_CHECKING:
    from stedi.models import (
        CreateEligibilityCheckOutput,
        EligibilityCheckBenefits,
        EligibilityCheckRelatedEntity,
    )

#: Coverage statuses the vendor reports, in the chart's three words. Every
#: ``ACTIVE_*`` variant answers the chart's question the same way — the plan
#: pays — and the distinctions between them (capitated, full risk, pending
#: investigation) are about how the payer settles internally.
_ACTIVE_PREFIX = "ACTIVE"
_INACTIVE_PREFIX = "INACTIVE"

#: What the vendor calls a benefit that is "what is left", as opposed to the
#: plan-year total. A deductible the chart can act on is the remaining one.
_REMAINING = "REMAINING"

#: The quantity qualifier that means "visits", as opposed to days or dollars.
_VISITS = "VISITS"

_PRIOR_AUTH_REQUIRED = "REQUIRED"


def _enum_value(value: Any) -> str | None:
    """The vendor's enums are ``str`` enums; unknown payers send bare strings."""
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _dollars_to_cents(amount: str | None) -> int | None:
    """``"25.00"`` as ``2500``.

    The vendor sends decimal strings whose precision is whatever the payer
    sent (``25``, ``25.0``, ``25.00`` all occur), so this rounds rather than
    truncating and never uses float arithmetic for the cents themselves.
    """
    if amount is None:
        return None
    try:
        return round(float(amount) * 100)
    except ValueError:
        return None


def _percent(value: str | None) -> float | None:
    """``"0.2"`` as ``20.0`` — the chart shows a percentage, not a fraction."""
    if value is None:
        return None
    try:
        return round(float(value) * 100, 2)
    except ValueError:
        return None


def _status_from(benefits: EligibilityCheckBenefits | None) -> EligibilityStatus:
    if benefits is None or not benefits.statuses:
        return "unknown"
    for entry in benefits.statuses:
        status = _enum_value(entry.status) or ""
        if status.startswith(_ACTIVE_PREFIX):
            return "active"
    for entry in benefits.statuses:
        status = _enum_value(entry.status) or ""
        if status.startswith(_INACTIVE_PREFIX):
            return "inactive"
    return "unknown"


def _entity_name(entity: EligibilityCheckRelatedEntity | Any) -> str | None:
    """A related entity's name, whether the payer sent a person or an organisation.

    The vendor models this as a tagged union whose variants each carry their
    payload as ``value``: an organisation's is the name itself, a person's is
    a name object. Which variant arrived is the payer's choice, so both are
    read rather than assumed.
    """
    name = getattr(entity, "name", None)
    if name is None:
        return None
    value = getattr(name, "value", None)
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    parts = [getattr(value, "first_name", None), getattr(value, "last_name", None)]
    return " ".join(part for part in parts if part) or None


def _carveout_from(
    benefits: EligibilityCheckBenefits | None, *, answering_payer_id: str | None
) -> CarveoutAdministrator | None:
    """The payer that administers this benefit, when it is not the one asked.

    The vendor documents ``other_or_additional_payer`` as the
    coordination-of-benefits signal. An entry naming the payer we just asked
    is not a carve-out — it is that payer describing itself — so entries whose
    payer id matches the answering payer are passed over.
    """
    if benefits is None or not benefits.other_or_additional_payer:
        return None
    for benefit in benefits.other_or_additional_payer:
        for entity in benefit.related_entities or ():
            payer_id = entity.payer_id
            if payer_id and answering_payer_id and payer_id == answering_payer_id:
                continue
            name = _entity_name(entity)
            if name:
                return CarveoutAdministrator(name=name, payer_id=payer_id)
    return None


def _visit_limit_from(benefits: EligibilityCheckBenefits | None) -> VisitLimit | None:
    if benefits is None or not benefits.limitations:
        return None
    remaining: int | None = None
    total: int | None = None
    for limitation in benefits.limitations:
        quantity = limitation.quantity
        if quantity is None or _enum_value(quantity.qualifier) != _VISITS:
            continue
        try:
            count = int(float(quantity.value)) if quantity.value is not None else None
        except ValueError:
            continue
        if count is None:
            continue
        if _enum_value(limitation.time_period) == _REMAINING:
            remaining = count
        else:
            total = count
    if remaining is None and total is None:
        return None
    return VisitLimit(remaining=remaining, total=total)


def _deductible_remaining_cents(benefits: EligibilityCheckBenefits | None) -> int | None:
    """What is left on the deductible, never the plan-year total.

    A payer commonly reports both. Reporting the total as though it were the
    remainder would tell a client they owe the whole deductible again, so an
    entry that does not say ``REMAINING`` is not used.
    """
    if benefits is None or not benefits.deductible:
        return None
    for entry in benefits.deductible:
        if _enum_value(entry.time_period) == _REMAINING:
            return _dollars_to_cents(entry.amount)
    return None


def _requires_authorization(benefits: EligibilityCheckBenefits | None) -> bool | None:
    if benefits is None:
        return None
    for group in (benefits.statuses or (), benefits.co_payment or (), benefits.limitations or ()):
        for entry in group:
            indicator = _enum_value(entry.prior_auth_indicator)
            if indicator is not None:
                return indicator == _PRIOR_AUTH_REQUIRED
    return None


def outcome_from_sdk(
    output: CreateEligibilityCheckOutput, *, stored: dict[str, Any]
) -> EligibilityOutcome:
    """The vendor's eligibility answer, as the chart's outcome.

    ``errors`` on the response are AAA rejections: the payer refused the
    inquiry rather than answering it. That is reported as ``error`` even when
    a plan is also present, because a refused inquiry has not told us the
    plan is active.
    """
    aaa_errors = [
        AaaError(
            code=error.code,
            description=error.description,
            followup_action=error.followup_action,
            resolution=error.possible_resolutions,
        )
        for error in output.errors or ()
    ]

    # The payer groups its benefits by plan; a client with two plans on one
    # card is rare and the chart has room for one answer, so the first is the
    # one read.
    plans = output.plans or ()
    plan = plans[0] if plans else None
    benefits = plan.benefits if plan is not None else None
    payer_id = output.payer_id

    copay = next(iter(benefits.co_payment or ()), None) if benefits else None
    coinsurance = next(iter(benefits.co_insurance or ()), None) if benefits else None

    return EligibilityOutcome(
        status="error" if aaa_errors else _status_from(benefits),
        payer_name=_entity_name(output.payer) if output.payer is not None else None,
        plan_name=plan.name if plan is not None else None,
        plan_begin=_plan_begin(output),
        copay_cents=_dollars_to_cents(copay.amount) if copay is not None else None,
        coinsurance_pct=_percent(coinsurance.percent) if coinsurance is not None else None,
        deductible_remaining_cents=_deductible_remaining_cents(benefits),
        visit_limit=_visit_limit_from(benefits),
        requires_authorization=_requires_authorization(benefits),
        carveout_administrator=_carveout_from(benefits, answering_payer_id=payer_id),
        aaa_errors=aaa_errors,
        stored=stored,
    )


def _plan_begin(output: CreateEligibilityCheckOutput) -> str | None:
    """When the payer says the plan started, if it said."""
    subscriber = output.subscriber
    dates = getattr(subscriber, "dates", None) if subscriber is not None else None
    plan = getattr(dates, "plan", None) if dates is not None else None
    start = getattr(plan, "start", None) if plan is not None else None
    return str(start) if start else None
