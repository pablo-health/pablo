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

Severity = Literal["critical", "routine"]
"""Tone tier consumed by downstream reminder dispatchers.

``critical`` items are livelihood / legal-exposure: missing them stops
the therapist from practicing, billing, or being insured (license,
malpractice, payer credentialing). ``routine`` items are compliance
hygiene where a missed deadline is an audit risk but doesn't immediately
halt the business (annual training, BAA chasing, SRA documentation).

Downstream reminder dispatchers use this to pick a tonally-appropriate
email template (the playful Pablo voice is fine for routine items, but
reads wrong on a license-lapse reminder).
"""


def edition_at_least(have: Edition, need: Edition) -> bool:
    """Return True if ``have`` includes everything ``need`` requires."""
    return _EDITION_RANK[have] >= _EDITION_RANK[need]


ProviderTypeFilter = tuple[str, ...]
"""Which provider types can see a template.

The canonical values are ``"therapist"``, ``"prescriber"``, and
``"both"``.  The default (``("therapist", "prescriber", "both")``) means
every user type sees the template, preserving backward-compatibility for
all existing entries.  Templates aimed specifically at prescribers set
``provider_types=("prescriber", "both")``.

Filtering is done at display/listing time (``list_templates_for_edition``).
A ``None`` caller provider_type is treated as "show everything," so
deployments that don't use the provider-type field keep the full catalog.
"""


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

    severity: Severity
    """Tone tier for downstream reminders. See ``Severity`` docstring."""

    provider_types: ProviderTypeFilter = ("therapist", "prescriber", "both")
    """Provider types that see this template.

    Defaults to all provider types so every existing entry remains
    universally visible without code changes. Prescriber-specific
    templates (DEA registration, board certification, etc.) narrow this
    to ``("prescriber", "both")``.

    Evaluated by ``list_templates_for_edition``: a caller whose
    ``provider_type`` is ``None`` receives all templates regardless of
    this field (backward-compatible with deployments that don't collect
    provider type).
    """


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
        severity="critical",
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
        severity="critical",
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
        severity="critical",
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
        severity="routine",
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
        severity="critical",
    ),
    # --- Prescriber-specific (core, all editions) ----------------------
    ComplianceTemplate(
        item_type="dea_registration",
        label="DEA registration renewal",
        description=(
            "Federal controlled-substance registration. Renewed every "
            "3 years; practicing without a valid registration is a federal "
            "violation."
        ),
        cadence_days=1095,
        reminder_windows=(90, 60, 30, 0),
        multi_instance=False,
        min_edition="core",
        sort_order=55,
        severity="critical",
        provider_types=("prescriber", "both"),
    ),
    ComplianceTemplate(
        item_type="dea_mate_training",
        label="DEA MATE training",
        description=(
            "One-time 8-hour training on substance use disorder treatment "
            "required of DEA registrants who prescribe controlled substances. "
            "Stored for reference; no recurring reminder."
        ),
        cadence_days=None,
        reminder_windows=(),
        multi_instance=False,
        min_edition="core",
        sort_order=57,
        severity="critical",
        provider_types=("prescriber", "both"),
    ),
    ComplianceTemplate(
        item_type="board_certification",
        label="Board certification / recertification",
        description=(
            "Specialty board certification renewal. Cycle length varies by "
            "board (commonly every 5 years); check your board's requirements."
        ),
        cadence_days=1825,
        reminder_windows=(180, 90, 30),
        multi_instance=False,
        min_edition="core",
        sort_order=58,
        severity="critical",
        provider_types=("prescriber", "both"),
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
        severity="routine",
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
        severity="routine",
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
        severity="critical",
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
        severity="routine",
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
        severity="critical",
    ),
    # --- Practice tier additions ---------------------------------------
    ComplianceTemplate(
        item_type="security_risk_assessment",
        label="Annual security risk assessment",
        description=(
            "HIPAA Security Rule § 164.308(a)(1)(ii)(A) — required annually for covered entities."
        ),
        cadence_days=365,
        reminder_windows=(60, 30, 0),
        multi_instance=False,
        min_edition="practice",
        sort_order=110,
        severity="routine",
    ),
    ComplianceTemplate(
        item_type="vendor_inventory",
        label="Annual vendor inventory review",
        description=(
            "Refresh the inventory of every vendor that touches business or "
            "PHI workflows; verify each one's BAA status or non-PHI "
            "designation. Pairs with the per-vendor `baa` items."
        ),
        cadence_days=365,
        reminder_windows=(60, 30, 0),
        multi_instance=False,
        min_edition="practice",
        sort_order=120,
        severity="routine",
    ),
    ComplianceTemplate(
        item_type="audit_log_review",
        label="Audit log review",
        description=(
            "Periodic review of system activity / audit log streams. "
            "HIPAA Security Rule § 164.308(a)(1)(ii)(D) Information System "
            "Activity Review and § 164.312(b) Audit Controls."
        ),
        cadence_days=30,
        reminder_windows=(3, 0),
        multi_instance=False,
        min_edition="practice",
        sort_order=130,
        severity="routine",
    ),
    ComplianceTemplate(
        item_type="backup_verification",
        label="Backup restore verification",
        description=(
            "Test-restore from backups to confirm they are usable. "
            "§ 164.308(a)(7)(ii)(A) Data Backup Plan + (D) Testing and "
            "Revision Procedures."
        ),
        cadence_days=90,
        reminder_windows=(14, 0),
        multi_instance=False,
        min_edition="practice",
        sort_order=140,
        severity="routine",
    ),
    ComplianceTemplate(
        item_type="dr_test",
        label="Disaster recovery / contingency tabletop",
        description=(
            "Walk through one scenario from the contingency plan and "
            "document the result. § 164.308(a)(7)(ii)(B) Disaster Recovery "
            "Plan + (D) Testing and Revision Procedures."
        ),
        cadence_days=365,
        reminder_windows=(60, 30, 0),
        multi_instance=False,
        min_edition="practice",
        sort_order=150,
        severity="routine",
    ),
    ComplianceTemplate(
        item_type="vuln_scan",
        label="Vulnerability scan review",
        description=(
            "Periodic review of vulnerability-scan output (container, "
            "dependency, and code scans). § 164.308(a)(1)(ii)(B) Risk "
            "Management; required if you self-host any part of the stack."
        ),
        cadence_days=90,
        reminder_windows=(14, 0),
        multi_instance=False,
        min_edition="practice",
        sort_order=160,
        severity="routine",
    ),
    ComplianceTemplate(
        item_type="vendor_verification",
        label="Vendor verification (per vendor)",
        description=(
            "Annual written analysis + officer certification confirming a "
            "subprocessor's safeguards. Add one per vendor in the BA chain. "
            "Tracks the 2026 NPRM § 164.314(b)(2)(ii) verification flow."
        ),
        cadence_days=365,
        reminder_windows=(60, 30, 0),
        multi_instance=True,
        min_edition="practice",
        sort_order=170,
        severity="routine",
    ),
    ComplianceTemplate(
        item_type="asset_inventory_review",
        label="Technology asset inventory review",
        description=(
            "Refresh the written inventory of devices, systems, and media "
            "that touch PHI. § 164.310(d)(1) Device and Media Controls; "
            "2026 NPRM § 164.308(a)(1)(ii)(A) written asset inventory."
        ),
        cadence_days=365,
        reminder_windows=(60, 30, 0),
        multi_instance=False,
        min_edition="practice",
        sort_order=180,
        severity="routine",
    ),
    ComplianceTemplate(
        item_type="compliance_audit",
        label="Internal HIPAA compliance audit",
        description=(
            "Internal audit comparing implemented controls against the "
            "Security Rule, distinct from the annual risk analysis. "
            "2026 NPRM § 164.308(a)(14)."
        ),
        cadence_days=365,
        reminder_windows=(90, 30, 0),
        multi_instance=False,
        min_edition="practice",
        sort_order=190,
        severity="routine",
    ),
    # Review deadline for a supervision / oversight relationship (physician
    # delegation, collaborative practice, pre-licensure supervision). Items of
    # this type are created automatically alongside a supervision relationship
    # so the relationship's review date flows through the reminder cron; the
    # cadence is None because the review date is set per relationship, not on a
    # fixed cycle. Visible to every role — supervision applies to prescribers
    # (delegation) and pre-licensure clinicians alike.
    ComplianceTemplate(
        item_type="supervision_review",
        label="Supervision agreement review",
        description=(
            "Review date for a supervision or oversight relationship "
            "(delegation, collaborative practice, or clinical supervision). "
            "Lapsing one can suspend the authority it grants."
        ),
        cadence_days=None,
        reminder_windows=(90, 60, 30, 0),
        multi_instance=True,
        min_edition="core",
        sort_order=200,
        severity="critical",
    ),
    # --- Escape hatch ---------------------------------------------------
    # Free-form custom reminder. The user supplies their own per-instance
    # label (multi_instance=True); we only enforce a sensible default
    # cadence so the reminder cron still has something to fire on. Sort
    # last so it never displaces the structured catalog entries.
    ComplianceTemplate(
        item_type="custom",
        label="Custom reminder",
        description=(
            "Anything not in the catalog above — a one-off deadline, an "
            "internal review, a vendor follow-up. Set your own label and date."
        ),
        cadence_days=None,
        reminder_windows=(30, 7, 0),
        multi_instance=True,
        min_edition="core",
        sort_order=9000,
        severity="routine",
    ),
)


def list_templates_for_edition(
    edition: Edition,
    provider_type: str | None = None,
) -> list[ComplianceTemplate]:
    """Return templates visible to the given edition, sorted for display.

    Args:
        edition: The current deployment edition (``core``, ``solo``, or
            ``practice``).  A template is included only when the edition
            meets its ``min_edition`` requirement.
        provider_type: The caller's provider type (``"therapist"``,
            ``"prescriber"``, ``"both"``, or ``None``).  When ``None``
            every template passes the provider-type check, preserving
            backward-compatibility for deployments that do not collect
            provider type.  Otherwise a template is included only when
            ``provider_type`` appears in its ``provider_types`` tuple.
    """

    def _visible(t: ComplianceTemplate) -> bool:
        return edition_at_least(edition, t.min_edition) and (
            provider_type is None or provider_type in t.provider_types
        )

    return sorted(
        (t for t in _TEMPLATES if _visible(t)),
        key=lambda t: t.sort_order,
    )


def get_template(item_type: str) -> ComplianceTemplate | None:
    """Look up a template by ``item_type``."""
    for t in _TEMPLATES:
        if t.item_type == item_type:
            return t
    return None
