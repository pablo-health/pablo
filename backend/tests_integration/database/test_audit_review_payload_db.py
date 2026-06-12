# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``AuditReviewService.compute_payload()`` end-to-end against Postgres.

The flag-specific modules pin individual flag definitions; this one pins
the *payload contract* the daily-review job ships to the LLM:

  - every entry carries the three behavioral booleans plus the audit
    metadata columns, and ``user_aggregates`` is always a list
  - nothing PHI-shaped survives composition — no PHI field *name* as a
    key (recursively, ``changes`` included) and no patient/user name
    *value* anywhere in the serialized payload
  - warmup means "cannot judge", not "all clear": a brand-new user with
    brand-new patients gets all-False flags even on a screaming access
    pattern
  - ``window_hours`` actually slices the window, and the window/baseline
    boundary moves with it (the same access flips novelty depending on
    which side of the boundary its prior touch lands)
  - an empty database produces the empty-but-well-formed payload
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.models.audit import PHI_FIELD_NAMES, AuditLogEntry

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .conftest import AuditReviewHarness

# Every entry must carry the audit metadata plus the three booleans the
# review prompt keys on.
REQUIRED_ENTRY_KEYS = frozenset(
    {
        "id",
        "timestamp",
        "user_id",
        "action",
        "patient_id",
        "ip_address",
        "user_agent",
        "is_novel_user_patient",
        "is_same_last_name",
        "is_no_treatment_relationship",
    }
)

FLAG_KEYS = (
    "is_novel_user_patient",
    "is_same_last_name",
    "is_no_treatment_relationship",
)


def _rows_for(payload: dict, user_id: str, patient_id: str) -> list[dict]:
    return [
        e for e in payload["entries"] if e["user_id"] == user_id and e["patient_id"] == patient_id
    ]


def _walk_keys(node: Any) -> Iterator[str]:
    """Yield every dict key in the payload, recursively (lists included)."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_keys(item)


def _audit_update_with_changes(
    h: AuditReviewHarness, user_id: str, patient_id: str, changes: dict
) -> None:
    """Append a PATIENT_UPDATED row carrying a ``changes`` dict.

    The harness ``audit()`` helper doesn't expose ``changes``; the
    PHI-free contract has to cover nested dicts, so build the entry
    directly (same defaults the harness uses).
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    h.audit_repo.append(
        AuditLogEntry(
            user_id=user_id,
            action="patient_updated",
            resource_type="patient",
            resource_id=patient_id,
            patient_id=patient_id,
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            ip_address="203.0.113.10",
            user_agent="pytest-integration/1.0",
            changes=changes,
        )
    )


class TestPayloadShape:
    def test_every_entry_has_flags_and_metadata(self, audit_review: AuditReviewHarness) -> None:
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        patient = h.seed_patient(owner, created_days_ago=60)
        h.audit(owner, "patient_viewed", patient_id=patient)
        # Patient-less admin row must satisfy the same shape (patient_id
        # key present, just None).
        h.audit(owner, "export_queue_viewed")

        payload = h.payload(window_hours=24)

        assert isinstance(payload["user_aggregates"], list)
        assert len(payload["entries"]) == 2
        for entry in payload["entries"]:
            assert set(entry) >= REQUIRED_ENTRY_KEYS
            for flag in FLAG_KEYS:
                assert isinstance(entry[flag], bool)


class TestPayloadIsPhiFree:
    def test_no_phi_keys_and_no_name_values_anywhere(
        self, audit_review: AuditReviewHarness
    ) -> None:
        h = audit_review
        # Distinctive synthetic names: none of these tokens can appear
        # in a UUID / timestamp / IP by accident.
        owner = h.seed_user(name="Priya Vexworth")
        snooper = h.seed_user(name="Sam Quibble")
        relative = h.seed_patient(owner, last_name="Vexworth", created_days_ago=60)
        other = h.seed_patient(owner, last_name="Zylkander", created_days_ago=60)
        h.audit(owner, "patient_viewed", patient_id=relative)
        h.audit(snooper, "patient_viewed", patient_id=other)
        _audit_update_with_changes(
            h,
            owner,
            other,
            {"changed_fields": ["status"], "context": {"source": "integration"}},
        )

        payload = h.payload(window_hours=24)
        assert payload["entries"]

        # The same-surname signal is derived from the names …
        (relative_row,) = _rows_for(payload, owner, relative)
        assert relative_row["is_same_last_name"] is True

        # … but only the boolean lands in the payload: no PHI field name
        # as a key, anywhere (changes dicts included) …
        leaked_keys = set(_walk_keys(payload)) & PHI_FIELD_NAMES
        assert not leaked_keys, f"PHI field names leaked as keys: {leaked_keys}"

        # … and no name value, anywhere in the serialized payload.
        dumped = json.dumps(payload, default=str).lower()
        for name_token in ("synthetic", "vexworth", "zylkander", "priya", "quibble"):
            assert name_token not in dumped, f"name value {name_token!r} leaked"


class TestWarmupMeansCannotJudge:
    def test_screaming_pattern_yields_no_flags_on_cold_system(
        self, audit_review: AuditReviewHarness
    ) -> None:
        """Sparse history must yield False — "cannot judge", not "all clear".

        A brand-new user fanning out over ten distinct patients is
        exactly the pattern the flags exist to surface — but with
        yesterday-old patients and a one-day audit baseline, every
        warmup gate (MIN_USER_BASELINE_DAYS, PATIENT_INTAKE_SUPPRESSION_DAYS,
        MIN_APPOINTMENTS_FOR_CARETEAM_CHECK) holds it down.
        """
        h = audit_review
        user = h.seed_user(name="Nia Newhire")
        patients = [
            h.seed_patient(user, last_name=f"Cohort{i}", created_days_ago=1) for i in range(10)
        ]
        for patient in patients:
            h.audit(user, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)

        # Only today's ten views are in the window (creates were yesterday).
        assert len(payload["entries"]) == 10
        for entry in payload["entries"]:
            for flag in FLAG_KEYS:
                assert entry[flag] is False, f"{flag} fired during warmup for {entry['action']}"
        assert payload["user_aggregates"] == []


class TestWindowFiltering:
    def test_window_hours_slices_entries_and_moves_the_baseline(
        self, audit_review: AuditReviewHarness
    ) -> None:
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        snooper = h.seed_user(name="Sam Snooper")
        patient = h.seed_patient(owner, created_days_ago=60)
        # Snooper has 30 days of unrelated history (past warmup) …
        h.audit(snooper, "patient_viewed", days_ago=30)
        # … then views the patient 30 hours ago and again today.
        h.audit(snooper, "patient_viewed", patient_id=patient, hours_ago=30)
        h.audit(snooper, "patient_viewed", patient_id=patient)

        narrow = h.payload(window_hours=24)
        wide = h.payload(window_hours=72)

        # The 30-hour-old row is outside a 24h window, inside a 72h one.
        assert len(_rows_for(narrow, snooper, patient)) == 1
        assert len(_rows_for(wide, snooper, patient)) == 2

        # Flags are recomputed against the window-relative baseline:
        # at 24h the 30-hour-old view sits in the *baseline*, so the
        # pair is already known and today's view is not novel; at 72h
        # both views are in-window with no baseline history, so both
        # are first touches.
        (narrow_row,) = _rows_for(narrow, snooper, patient)
        assert narrow_row["is_novel_user_patient"] is False
        assert all(
            row["is_novel_user_patient"] is True for row in _rows_for(wide, snooper, patient)
        )


class TestEmptyDatabase:
    def test_empty_database_yields_empty_payload(self, audit_review: AuditReviewHarness) -> None:
        payload = audit_review.payload(window_hours=24)
        assert payload == {"entries": [], "user_aggregates": []}
