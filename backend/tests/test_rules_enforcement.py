# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for layer 3 of the rules engine — enforcement.

Cover the predicate DSL, metadata -> spec parsing, per-item status
resolution (na / satisfied / missing via trigger, computed check, or
evidence), and the end-to-end finalization gate on a realistic
controlled-substance prescribing ruleset.
"""

from __future__ import annotations

from datetime import date

import pytest
from app.rules import (
    AppliesWhen,
    EnforcementReport,
    FlagBehavior,
    ItemStatus,
    RequirementLevel,
    RuleContext,
    RuleItem,
    Ruleset,
    enforcement_spec,
    evaluate_enforcement,
    evaluate_item,
    evaluate_predicate,
)

# --------------------------------------------------------------------------
# Predicate DSL
# --------------------------------------------------------------------------


def test_predicate_comparison_operators() -> None:
    facts = {"n": 5}
    assert evaluate_predicate({"field": "n", "op": "gt", "value": 3}, facts)
    assert not evaluate_predicate({"field": "n", "op": "gt", "value": 5}, facts)
    assert evaluate_predicate({"field": "n", "op": "gte", "value": 5}, facts)
    assert evaluate_predicate({"field": "n", "op": "lte", "value": 5}, facts)
    assert evaluate_predicate({"field": "n", "op": "lt", "value": 6}, facts)
    assert evaluate_predicate({"field": "n", "op": "eq", "value": 5}, facts)
    assert evaluate_predicate({"field": "n", "op": "ne", "value": 4}, facts)


def test_predicate_in_operator() -> None:
    facts = {"sched": "II"}
    assert evaluate_predicate({"field": "sched", "op": "in", "value": ["II", "III"]}, facts)
    assert not evaluate_predicate({"field": "sched", "op": "in", "value": ["IV", "V"]}, facts)
    # A string fact must not be treated as an iterable of characters.
    assert not evaluate_predicate({"field": "sched", "op": "in", "value": "IIIII"}, {"sched": "I"})


def test_predicate_missing_field_is_none() -> None:
    # Numeric comparison against an absent fact is fail-closed (False).
    assert not evaluate_predicate({"field": "absent", "op": "gt", "value": 3}, {})
    assert not evaluate_predicate({"field": "absent", "op": "lte", "value": 7}, {})
    # eq against None is a real comparison.
    assert evaluate_predicate({"field": "absent", "op": "eq", "value": None}, {})


def test_predicate_bool_not_treated_as_number() -> None:
    # A boolean flag compared with a numeric operator is a data error, not True.
    assert not evaluate_predicate({"field": "flag", "op": "gt", "value": 0}, {"flag": True})
    # eq still works for booleans.
    assert evaluate_predicate({"field": "flag", "op": "eq", "value": True}, {"flag": True})


def test_predicate_all_any_combinators() -> None:
    facts = {"a": 1, "b": 2}
    both = {"all": [{"field": "a", "op": "eq", "value": 1}, {"field": "b", "op": "eq", "value": 2}]}
    either = {
        "any": [{"field": "a", "op": "eq", "value": 9}, {"field": "b", "op": "eq", "value": 2}]
    }
    assert evaluate_predicate(both, facts)
    assert evaluate_predicate(either, facts)
    assert not evaluate_predicate({"all": [{"field": "a", "op": "eq", "value": 9}]}, facts)
    # Empty all -> True, empty any -> False.
    assert evaluate_predicate({"all": []}, facts)
    assert not evaluate_predicate({"any": []}, facts)


def test_predicate_unknown_operator_raises() -> None:
    with pytest.raises(ValueError, match="Unknown predicate operator"):
        evaluate_predicate({"field": "n", "op": "between", "value": 3}, {"n": 5})


# --------------------------------------------------------------------------
# Spec parsing
# --------------------------------------------------------------------------


def test_enforcement_spec_defaults_to_advisory() -> None:
    # An item with no enforcement metadata (e.g. a credentialing item) is
    # advisory: info / recommended, no trigger, no computed check, no evidence.
    spec = enforcement_spec(RuleItem(id="x", applies_when=AppliesWhen()))
    assert spec.flag_behavior is FlagBehavior.INFO
    assert spec.requirement_level is RequirementLevel.RECOMMENDED
    assert spec.trigger is None
    assert spec.satisfied_when is None
    assert spec.requires_evidence is False


def test_enforcement_spec_parses_metadata() -> None:
    item = RuleItem(
        id="x",
        applies_when=AppliesWhen(),
        metadata={
            "flag_behavior": "hard_stop",
            "requirement_level": "conditional",
            "trigger": {"field": "prescription.days_supply", "op": "gt", "value": 3},
            "evidence": "MAPS query record",
        },
    )
    spec = enforcement_spec(item)
    assert spec.flag_behavior is FlagBehavior.HARD_STOP
    assert spec.requirement_level is RequirementLevel.CONDITIONAL
    assert spec.trigger == {"field": "prescription.days_supply", "op": "gt", "value": 3}
    assert spec.requires_evidence is True


# --------------------------------------------------------------------------
# Item status resolution
# --------------------------------------------------------------------------


def _item(item_id: str, metadata: dict) -> RuleItem:
    return RuleItem(id=item_id, applies_when=AppliesWhen(), metadata=metadata)


def test_item_conditional_not_triggered_is_na() -> None:
    item = _item(
        "maps_review",
        {
            "flag_behavior": "hard_stop",
            "trigger": {"field": "prescription.days_supply", "op": "gt", "value": 3},
            "evidence": "MAPS query",
        },
    )
    ev = evaluate_item(item, {"prescription.days_supply": 3}, evidence=())
    assert ev.status is ItemStatus.NA
    assert ev.blocking is False


def test_item_triggered_missing_evidence_blocks() -> None:
    item = _item(
        "maps_review",
        {
            "flag_behavior": "hard_stop",
            "trigger": {"field": "prescription.days_supply", "op": "gt", "value": 3},
            "evidence": "MAPS query",
        },
    )
    ev = evaluate_item(item, {"prescription.days_supply": 5}, evidence=())
    assert ev.status is ItemStatus.MISSING
    assert ev.blocking is True


def test_item_triggered_with_evidence_satisfied() -> None:
    item = _item(
        "maps_review",
        {
            "flag_behavior": "hard_stop",
            "trigger": {"field": "prescription.days_supply", "op": "gt", "value": 3},
            "evidence": "MAPS query",
        },
    )
    ev = evaluate_item(item, {"prescription.days_supply": 5}, evidence={"maps_review": "ehr://q/1"})
    assert ev.status is ItemStatus.SATISFIED
    assert ev.blocking is False


def test_item_satisfied_when_computed_check() -> None:
    item = _item(
        "sch2_no_refill",
        {
            "flag_behavior": "hard_stop",
            "satisfied_when": {"field": "prescription.refills", "op": "eq", "value": 0},
        },
    )
    assert evaluate_item(item, {"prescription.refills": 0}, ()).status is ItemStatus.SATISFIED
    bad = evaluate_item(item, {"prescription.refills": 2}, ())
    assert bad.status is ItemStatus.MISSING
    assert bad.blocking is True


def test_item_soft_warn_missing_is_warning_not_blocking() -> None:
    item = _item("start_talking", {"flag_behavior": "soft_warn", "evidence": "consent form"})
    ev = evaluate_item(item, {}, evidence=())
    assert ev.status is ItemStatus.MISSING
    assert ev.blocking is False
    assert ev.warning is True


def test_item_evidence_mapping_or_set() -> None:
    item = _item("dea_valid", {"flag_behavior": "hard_stop", "evidence": "DEA record"})
    assert evaluate_item(item, {}, evidence=["dea_valid"]).status is ItemStatus.SATISFIED
    assert evaluate_item(item, {}, evidence={"dea_valid": "x"}).status is ItemStatus.SATISFIED
    assert evaluate_item(item, {}, evidence={"dea_valid": None}).status is ItemStatus.MISSING


# --------------------------------------------------------------------------
# End-to-end enforcement
# --------------------------------------------------------------------------


def _prescribing_ruleset() -> Ruleset:
    """A trimmed MI-style controlled-substance ruleset for enforcement tests."""

    cs = ("II", "III", "IV", "V")
    return Ruleset(
        id="TEST-RX",
        version="2026.06",
        effective_date=date(2026, 6, 1),
        items=[
            RuleItem(
                id="dea_valid",
                applies_when=AppliesWhen(provider_type=("prescriber",), schedule=cs),
                metadata={
                    "flag_behavior": "hard_stop",
                    "requirement_level": "required",
                    "evidence": "DEA record",
                },
            ),
            RuleItem(
                id="maps_review",
                applies_when=AppliesWhen(provider_type=("prescriber",), schedule=cs),
                metadata={
                    "flag_behavior": "hard_stop",
                    "requirement_level": "conditional",
                    "trigger": {"field": "prescription.days_supply", "op": "gt", "value": 3},
                    "evidence": "MAPS query",
                },
            ),
            RuleItem(
                id="sch2_no_refill",
                applies_when=AppliesWhen(provider_type=("prescriber",), schedule=("II",)),
                metadata={
                    "flag_behavior": "hard_stop",
                    "requirement_level": "required",
                    "satisfied_when": {"field": "prescription.refills", "op": "eq", "value": 0},
                },
            ),
            RuleItem(
                id="start_talking",
                applies_when=AppliesWhen(provider_type=("prescriber",), drug_class=("opioid",)),
                metadata={
                    "flag_behavior": "soft_warn",
                    "requirement_level": "conditional",
                    "trigger": {"field": "context.first_in_course", "op": "eq", "value": True},
                    "evidence": "consent form",
                },
            ),
        ],
    )


def test_enforcement_resolves_applicability_first() -> None:
    # A non-controlled prescription (schedule "none") matches nothing -> the
    # whole module is off, nothing to enforce, finalization clear.
    ruleset = _prescribing_ruleset()
    ctx = RuleContext(provider_type="prescriber", schedule="none", drug_class="other")
    report = evaluate_enforcement(ruleset, ctx, facts={}, evidence=())
    assert report.items == []
    assert report.can_finalize is True


def test_enforcement_blocks_until_evidence_supplied() -> None:
    ruleset = _prescribing_ruleset()
    ctx = RuleContext(provider_type="prescriber", schedule="II", drug_class="stimulant")
    facts = {"prescription.days_supply": 30, "prescription.refills": 0}

    # No evidence yet: dea_valid + maps_review (triggered at 30-day) are
    # missing hard stops; sch2_no_refill is satisfied (refills == 0).
    report = evaluate_enforcement(ruleset, ctx, facts, evidence=())
    by_id = {e.item_id: e for e in report.items}
    assert by_id["sch2_no_refill"].status is ItemStatus.SATISFIED
    assert by_id["dea_valid"].status is ItemStatus.MISSING
    assert by_id["maps_review"].status is ItemStatus.MISSING
    assert {e.item_id for e in report.blocking_items} == {"dea_valid", "maps_review"}
    assert report.can_finalize is False

    # Supply both evidence records -> finalization clears.
    cleared = evaluate_enforcement(
        ruleset, ctx, facts, evidence={"dea_valid": "x", "maps_review": "ehr://q/1"}
    )
    assert cleared.can_finalize is True
    assert cleared.blocking_items == []


def test_enforcement_maps_review_na_under_three_day_supply() -> None:
    ruleset = _prescribing_ruleset()
    ctx = RuleContext(provider_type="prescriber", schedule="II", drug_class="stimulant")
    facts = {"prescription.days_supply": 2, "prescription.refills": 0}
    report = evaluate_enforcement(ruleset, ctx, facts, evidence={"dea_valid": "x"})
    by_id = {e.item_id: e for e in report.items}
    assert by_id["maps_review"].status is ItemStatus.NA
    assert report.can_finalize is True


def test_enforcement_soft_warn_does_not_block() -> None:
    ruleset = _prescribing_ruleset()
    ctx = RuleContext(provider_type="prescriber", schedule="II", drug_class="opioid")
    facts = {
        "prescription.days_supply": 2,
        "prescription.refills": 0,
        "context.first_in_course": True,
    }
    report = evaluate_enforcement(ruleset, ctx, facts, evidence={"dea_valid": "x"})
    by_id = {e.item_id: e for e in report.items}
    # Start Talking consent is triggered + missing, but soft_warn -> a warning,
    # not a block.
    assert by_id["start_talking"].status is ItemStatus.MISSING
    assert by_id["start_talking"].warning is True
    assert {e.item_id for e in report.warnings} == {"start_talking"}
    assert report.can_finalize is True


def test_enforcement_report_is_empty_report_finalizable() -> None:
    assert EnforcementReport().can_finalize is True
