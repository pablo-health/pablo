# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Compliance template registry — the catalog of trackable items.

Templates are the *catalog* of compliance reminders Pablo knows about
(license renewal, CAQH attestation, etc.). User-entered values live in
the ``compliance_items`` table and reference a template by ``item_type``.

Adding a new compliance reminder is a single edit to ``_TEMPLATES`` —
no migration, no route changes. ``min_edition`` gates visibility:
templates marked ``solo`` only appear for hosted (Pablo Solo / Practice)
editions; ``practice`` only for the multi-therapist tier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Edition = Literal["core", "solo", "practice"]
"""Pablo edition — mirrors ``settings.pablo_edition``.

Hierarchy (lowest to highest feature set): core < solo < practice.
A template's ``min_edition`` declares the *minimum* tier that sees it;
higher tiers always inherit lower-tier templates.
"""

_EDITION_RANK: dict[Edition, int] = {"core": 0, "solo": 1, "practice": 2}


def edition_at_least(have: Edition, need: Edition) -> bool:
    """Return True if ``have`` includes everything ``need`` requires."""
    return _EDITION_RANK[have] >= _EDITION_RANK[need]


@dataclass(frozen=True)
class ComplianceTemplate:
    """One trackable compliance category.

    A template is metadata only — the user's actual date for, say, their
    license renewal lives in a ``compliance_items`` row referencing this
    template by ``item_type``.
    """

    item_type: str
    """Stable string ID. Stored on ``compliance_items.item_type``. Never
    rename a value once shipped — existing user rows reference it."""

    label: str
    """Default human label shown in the wizard ("Professional license").
    The user can override per-instance for ``multi_instance`` items
    (e.g. "BAA — Twilio")."""

    description: str
    """One-line explanation shown in the wizard."""

    cadence_days: int | None
    """Recurring cadence in days (CAQH = 120, HIPAA training = 365).
    ``None`` = fixed expiration date entered by the user (license,
    insurance — no automatic renewal cycle)."""

    reminder_windows: tuple[int, ...]
    """Days-before-due to surface on the dashboard, ordered urgent-first.
    ``(0,)`` for "alert on the day"; empty tuple = informational only
    (e.g. NPI: stored, never reminded)."""

    multi_instance: bool
    """True for items where one therapist can have several rows
    (BAA per vendor, payer enrollment per insurer). Wizard offers
    "+ Add another" instead of pre-seeding a single row."""

    min_edition: Edition
    """Minimum Pablo edition that sees this template. ``core`` = visible
    everywhere; ``solo`` = hosted-only; ``practice`` = multi-therapist
    tier only."""

    sort_order: int
    """Display order in the wizard (ascending). Reserve gaps so new
    templates can slot in between existing ones without renumbering."""


_TEMPLATES: tuple[ComplianceTemplate, ...] = (
    ComplianceTemplate(
        item_type="license",
        label="Professional license",
        description="State board license to practice (LMFT, LCSW, LPC, PhD, etc.).",
        cadence_days=None,
        reminder_windows=(90, 60, 30, 0),
        multi_instance=False,
        min_edition="core",
        sort_order=10,
    ),
    ComplianceTemplate(
        item_type="liability_insurance",
        label="Malpractice / liability insurance",
        description="Professional liability coverage. Lapses leave you exposed.",
        cadence_days=None,
        reminder_windows=(60, 30, 0),
        multi_instance=False,
        min_edition="core",
        sort_order=20,
    ),
    ComplianceTemplate(
        item_type="caqh_attestation",
        label="CAQH re-attestation",
        description=(
            "Required every 120 days to stay credentialed with most "
            "commercial payers. The single most-missed credentialing task."
        ),
        cadence_days=120,
        reminder_windows=(30, 14, 7, 0),
        multi_instance=False,
        min_edition="core",
        sort_order=30,
    ),
    ComplianceTemplate(
        item_type="hipaa_training",
        label="HIPAA annual training",
        description="Most boards and payers expect documented annual refresh.",
        cadence_days=365,
        reminder_windows=(30, 0),
        multi_instance=False,
        min_edition="core",
        sort_order=40,
    ),
    ComplianceTemplate(
        item_type="npi",
        label="National Provider Identifier (NPI)",
        description="Stored for reference. No expiration — no reminders.",
        cadence_days=None,
        reminder_windows=(),
        multi_instance=False,
        min_edition="core",
        sort_order=50,
    ),
    # --- Solo (hosted) tier additions ----------------------------------
    ComplianceTemplate(
        item_type="ceu_credits",
        label="Continuing education credits",
        description="CEU progress toward your license-renewal cycle target.",
        cadence_days=None,
        reminder_windows=(120, 60, 30),
        multi_instance=False,
        min_edition="solo",
        sort_order=60,
    ),
    ComplianceTemplate(
        item_type="baa",
        label="Business Associate Agreement",
        description=(
            "BAA with a vendor that handles PHI (EHR, billing, fax, "
            "transcription). Add one per vendor."
        ),
        cadence_days=None,
        reminder_windows=(60, 30, 0),
        multi_instance=True,
        min_edition="solo",
        sort_order=70,
    ),
    ComplianceTemplate(
        item_type="payer_enrollment",
        label="Payer enrollment / revalidation",
        description=(
            "Medicare revalidates every 5 years; most commercial payers "
            "every 3. Add one per insurer."
        ),
        cadence_days=None,
        reminder_windows=(180, 90, 30, 0),
        multi_instance=True,
        min_edition="solo",
        sort_order=80,
    ),
    ComplianceTemplate(
        item_type="mandated_reporter_training",
        label="Mandated reporter training",
        description="State-specific renewal cadence; commonly every 1-3 years.",
        cadence_days=None,
        reminder_windows=(60, 30, 0),
        multi_instance=False,
        min_edition="solo",
        sort_order=90,
    ),
    ComplianceTemplate(
        item_type="telehealth_licensure",
        label="Telehealth licensure (per state)",
        description=(
            "If you see clients across state lines, track each state's "
            "license or compact authorization separately."
        ),
        cadence_days=None,
        reminder_windows=(90, 60, 30, 0),
        multi_instance=True,
        min_edition="solo",
        sort_order=100,
    ),
    # --- Practice tier additions ---------------------------------------
    ComplianceTemplate(
        item_type="security_risk_assessment",
        label="Annual security risk assessment",
        description=(
            "HIPAA Security Rule § 164.308(a)(1)(ii)(A) — required annually "
            "for covered entities."
        ),
        cadence_days=365,
        reminder_windows=(60, 30, 0),
        multi_instance=False,
        min_edition="practice",
        sort_order=110,
    ),
)


def list_templates_for_edition(edition: Edition) -> list[ComplianceTemplate]:
    """Return templates visible to the given edition, sorted for display."""
    return sorted(
        (t for t in _TEMPLATES if edition_at_least(edition, t.min_edition)),
        key=lambda t: t.sort_order,
    )


def get_template(item_type: str) -> ComplianceTemplate | None:
    """Look up a template by ``item_type``."""
    for t in _TEMPLATES:
        if t.item_type == item_type:
            return t
    return None
