# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the pure rules-engine core (applicability + versioned data)."""

from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

from app.rules import (
    AppliesWhen,
    RuleContext,
    RuleItem,
    Ruleset,
    applies,
    dump_ruleset,
    evaluate_applicability,
    load_ruleset,
    select_active_ruleset,
)

_EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "app" / "rules" / "rulesets" / "example.json"


def _item(item_id: str, applies_when: AppliesWhen) -> RuleItem:
    return RuleItem(id=item_id, applies_when=applies_when)


def load_ruleset_from_mapping(mapping: dict) -> Ruleset:
    """Helper: parse a dumped mapping by reloading it through the loader."""

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(mapping, handle)
        handle.flush()
        path = handle.name
    return load_ruleset(path)


# --- applies() across dimensions ---------------------------------------


def test_none_predicate_matches_anything() -> None:
    assert applies(AppliesWhen(), RuleContext()) is True
    assert applies(AppliesWhen(), RuleContext(provider_type="prescriber", state="XX")) is True


def test_provider_type_match_and_mismatch() -> None:
    pred = AppliesWhen(provider_type=("prescriber", "both"))
    assert applies(pred, RuleContext(provider_type="prescriber")) is True
    assert applies(pred, RuleContext(provider_type="both")) is True
    assert applies(pred, RuleContext(provider_type="therapist")) is False


def test_unset_context_value_fails_gated_dimension() -> None:
    pred = AppliesWhen(provider_type=("prescriber",))
    assert applies(pred, RuleContext()) is False


def test_state_dimension_independent_of_provider_type() -> None:
    pred = AppliesWhen(state=("XX",))
    assert applies(pred, RuleContext(state="XX")) is True
    assert applies(pred, RuleContext(state="YY")) is False
    assert applies(pred, RuleContext()) is False


def test_all_supplied_dimensions_must_match() -> None:
    pred = AppliesWhen(provider_type=("prescriber",), state=("XX",))
    assert applies(pred, RuleContext(provider_type="prescriber", state="XX")) is True
    # provider matches, state does not -> no match (logical AND)
    assert applies(pred, RuleContext(provider_type="prescriber", state="YY")) is False
    # state matches, provider does not -> no match
    assert applies(pred, RuleContext(provider_type="therapist", state="XX")) is False


def test_schedule_unset_means_schedule_gated_item_does_not_apply() -> None:
    schedule_gated = AppliesWhen(schedule=("II",))
    # Context with no schedule set must not pick up a schedule-gated item.
    assert applies(schedule_gated, RuleContext(provider_type="prescriber")) is False
    assert applies(schedule_gated, RuleContext(schedule="II")) is True
    assert applies(schedule_gated, RuleContext(schedule="IV")) is False


# --- evaluate_applicability() ------------------------------------------


def test_evaluate_applicability_filters_and_preserves_order() -> None:
    ruleset = Ruleset(
        id="rs",
        version="v1",
        effective_date=date(2026, 1, 1),
        items=[
            _item("national", AppliesWhen(provider_type=("prescriber", "both"))),
            _item("state-xx", AppliesWhen(provider_type=("prescriber",), state=("XX",))),
            _item("therapist-only", AppliesWhen(provider_type=("therapist",))),
            _item("schedule-gated", AppliesWhen(schedule=("II",))),
        ],
    )

    context = RuleContext(provider_type="prescriber", state="XX")
    applicable = evaluate_applicability(ruleset, context)

    assert [i.id for i in applicable] == ["national", "state-xx"]


def test_evaluate_applicability_empty_when_nothing_matches() -> None:
    ruleset = Ruleset(
        id="rs",
        version="v1",
        effective_date=date(2026, 1, 1),
        items=[_item("therapist-only", AppliesWhen(provider_type=("therapist",)))],
    )
    assert evaluate_applicability(ruleset, RuleContext(provider_type="prescriber")) == []


# --- select_active_ruleset() -------------------------------------------


def _rs(rs_id: str, effective: date) -> Ruleset:
    return Ruleset(id=rs_id, version=rs_id, effective_date=effective, items=[])


def _active(rulesets: list[Ruleset], on: date) -> Ruleset:
    active = select_active_ruleset(rulesets, on)
    assert active is not None
    return active


def test_select_active_picks_latest_effective_on_or_before() -> None:
    rulesets = [
        _rs("old", date(2025, 1, 1)),
        _rs("mid", date(2026, 1, 1)),
        _rs("new", date(2026, 6, 1)),
    ]
    assert _active(rulesets, date(2026, 3, 15)).id == "mid"
    # Exactly on the effective date counts as in force.
    assert _active(rulesets, date(2026, 6, 1)).id == "new"
    assert _active(rulesets, date(2026, 1, 1)).id == "mid"


def test_select_active_returns_none_before_any_effective() -> None:
    rulesets = [_rs("future", date(2027, 1, 1))]
    assert select_active_ruleset(rulesets, date(2026, 6, 1)) is None


def test_select_active_handles_unordered_input() -> None:
    rulesets = [
        _rs("new", date(2026, 6, 1)),
        _rs("old", date(2025, 1, 1)),
        _rs("mid", date(2026, 1, 1)),
    ]
    assert _active(rulesets, date(2026, 6, 30)).id == "new"


def test_select_active_empty_list() -> None:
    assert select_active_ruleset([], date(2026, 6, 1)) is None


# --- loader round-trip -------------------------------------------------


def test_load_example_ruleset() -> None:
    ruleset = load_ruleset(_EXAMPLE_PATH)
    assert ruleset.id == "EXAMPLE-2026.06"
    assert ruleset.version == "2026.06"
    assert ruleset.effective_date == date(2026, 6, 1)
    assert len(ruleset.items) == 3

    state_item = next(i for i in ruleset.items if i.id == "example-state-license")
    assert state_item.applies_when.provider_type == ("prescriber", "both")
    assert state_item.applies_when.state == ("XX",)
    # Unspecified dimensions load as "any".
    assert state_item.applies_when.schedule is None
    assert state_item.metadata is not None
    assert state_item.metadata["cadence_days"] == 730


def test_example_ruleset_evaluates_against_context() -> None:
    ruleset = load_ruleset(_EXAMPLE_PATH)
    prescriber_xx = evaluate_applicability(
        ruleset, RuleContext(provider_type="prescriber", state="XX")
    )
    assert {i.id for i in prescriber_xx} == {
        "example-national-credential",
        "example-state-license",
    }

    # A prescriber outside XX drops the state-gated item.
    prescriber_yy = evaluate_applicability(
        ruleset, RuleContext(provider_type="prescriber", state="YY")
    )
    assert {i.id for i in prescriber_yy} == {"example-national-credential"}


def test_loader_round_trip(tmp_path: Path) -> None:
    original = load_ruleset(_EXAMPLE_PATH)

    out_path = tmp_path / "round_trip.json"
    out_path.write_text(json.dumps(dump_ruleset(original)), encoding="utf-8")
    reloaded = load_ruleset(out_path)

    assert reloaded == original


def test_dump_omits_any_dimensions_and_optional_fields() -> None:
    ruleset = Ruleset(
        id="rs",
        version="v1",
        effective_date=date(2026, 1, 1),
        items=[_item("bare", AppliesWhen(provider_type=("prescriber",)))],
    )
    dumped = dump_ruleset(ruleset)
    item = dumped["items"][0]
    # Only the populated dimension is serialized.
    assert item["applies_when"] == {"provider_type": ["prescriber"]}
    # Optional fields left off when None.
    assert "authority_ref" not in item
    assert "metadata" not in item
    # And it still round-trips.
    assert load_ruleset_from_mapping(dumped) == ruleset
