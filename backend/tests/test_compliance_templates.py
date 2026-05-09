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
)


def test_every_template_has_a_severity() -> None:
    valid = set(get_args(Severity))
    for tmpl in _TEMPLATES:
        assert tmpl.severity in valid, (
            f"{tmpl.item_type} has severity={tmpl.severity!r}, "
            f"expected one of {sorted(valid)}"
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
    }
    actual_critical = {t.item_type for t in _TEMPLATES if t.severity == "critical"}
    assert actual_critical == expected_critical
