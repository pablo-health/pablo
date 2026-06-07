# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Static checks on the compliance template registry.

These are guard-rail tests for the catalog itself (not for any consumer
of it). The registry is pure data with no side effects, so the tests
don't need the heavyweight conftest fixtures.
"""

from __future__ import annotations

from typing import get_args

from app.compliance.templates import (
    _TEMPLATES,
    Severity,
    get_template,
    list_templates_for_edition,
)


def test_every_template_has_a_severity() -> None:
    valid = set(get_args(Severity))
    for tmpl in _TEMPLATES:
        assert tmpl.severity in valid, (
            f"{tmpl.item_type} has severity={tmpl.severity!r}, expected one of {sorted(valid)}"
        )


def test_get_template_round_trips_severity() -> None:
    for tmpl in _TEMPLATES:
        looked_up = get_template(tmpl.item_type)
        assert looked_up is not None
        assert looked_up.severity == tmpl.severity


def test_critical_items_are_livelihood_or_legal() -> None:
    """Lock in the initial severity assignments. Update this set when
    deliberately re-tiering an item — accidental drift will fail here.
    """
    expected_critical = {
        "license",
        "liability_insurance",
        "telehealth_licensure",
        "caqh_attestation",
        "payer_enrollment",
        "npi",
        # Prescriber-specific: federal registration + board credential
        "dea_registration",
        "dea_mate_training",
        "board_certification",
    }
    actual_critical = {t.item_type for t in _TEMPLATES if t.severity == "critical"}
    assert actual_critical == expected_critical


def test_provider_type_filter_therapist_excludes_prescriber_templates() -> None:
    """A therapist must NOT see DEA registration or other prescriber-only items."""
    therapist_types = {
        t.item_type for t in list_templates_for_edition("core", provider_type="therapist")
    }
    assert "dea_registration" not in therapist_types
    assert "dea_mate_training" not in therapist_types
    assert "board_certification" not in therapist_types
    # But therapist-universal items remain visible.
    assert "license" in therapist_types
    assert "liability_insurance" in therapist_types
    assert "npi" in therapist_types


def test_provider_type_filter_prescriber_sees_prescriber_templates() -> None:
    """A prescriber DOES see DEA registration and other prescriber-specific items."""
    prescriber_types = {
        t.item_type for t in list_templates_for_edition("core", provider_type="prescriber")
    }
    assert "dea_registration" in prescriber_types
    assert "dea_mate_training" in prescriber_types
    assert "board_certification" in prescriber_types
    # Universal items are also visible.
    assert "license" in prescriber_types
    assert "npi" in prescriber_types


def test_provider_type_filter_both_sees_prescriber_templates() -> None:
    """A user with provider_type='both' (dual-role) sees prescriber-specific items."""
    both_types = {t.item_type for t in list_templates_for_edition("core", provider_type="both")}
    assert "dea_registration" in both_types
    assert "dea_mate_training" in both_types
    assert "board_certification" in both_types


def test_provider_type_none_sees_all_templates() -> None:
    """A None provider_type returns all edition-visible templates (backward compat)."""
    all_types = {t.item_type for t in list_templates_for_edition("core", provider_type=None)}
    assert "dea_registration" in all_types
    assert "license" in all_types
    # Confirm this matches the unfiltered core set exactly.
    unfiltered = {t.item_type for t in list_templates_for_edition("core")}
    assert all_types == unfiltered


def test_prescriber_templates_default_provider_types_tuple() -> None:
    """Each new prescriber template carries the correct provider_types value."""
    for item_type in ("dea_registration", "dea_mate_training", "board_certification"):
        tmpl = get_template(item_type)
        assert tmpl is not None, f"Missing template: {item_type}"
        assert "prescriber" in tmpl.provider_types
        assert "therapist" not in tmpl.provider_types


def test_universal_templates_visible_to_all_provider_types() -> None:
    """Templates that predate provider-type filtering remain visible to all roles."""
    universal = ("license", "liability_insurance", "caqh_attestation", "npi")
    for provider_type in ("therapist", "prescriber", "both"):
        visible = {
            t.item_type for t in list_templates_for_edition("core", provider_type=provider_type)
        }
        for item_type in universal:
            assert item_type in visible, (
                f"Universal template '{item_type}' hidden from provider_type='{provider_type}'"
            )
