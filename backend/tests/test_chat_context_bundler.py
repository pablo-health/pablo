# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit + integration tests for the chat context bundler (THERAPY-r3c).

Covers the source registry, every v1 source loader, the truncation
policy, manifest construction, and the overflow error path. The
bundler operates on a :class:`NotesRepository` so these tests use the
in-memory repo and don't touch Postgres.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.models import Note, PatientDocument
from app.repositories import InMemoryNotesRepository, InMemoryPatientDocumentRepository
from app.services.chat_context_bundler import (
    CHARS_PER_TOKEN,
    DEFAULT_TOKEN_BUDGET,
    PASTED_TEXT_MAX_CHARS,
    PATIENT_DOCUMENTS_LIMIT_MAX,
    SOURCE_KEY_CURRENT_MEDICATIONS,
    SOURCE_KEY_LAB_VALUES_RECENT,
    SOURCE_KEY_MOST_RECENT_INTAKE,
    SOURCE_KEY_PASTED_TEXT,
    SOURCE_KEY_PATIENT_DOCUMENTS,
    SOURCE_KEY_PROGRESS_NOTES_EXPLICIT,
    SOURCE_KEY_PROGRESS_NOTES_RECENT,
    SOURCE_KEY_SAFETY_PLAN_ACTIVE,
    SOURCE_KEY_TREATMENT_PLAN_ACTIVE,
    SOURCE_KEY_VITALS_RECENT,
    ContextOverflowError,
    InvalidSelectionError,
    assemble_context_bundle,
    default_source_selection,
    estimate_tokens,
)

PATIENT_ID = "patient-bundler-1"
USER_ID = "clinician-bundler-1"


def _make_note(
    *,
    note_type: str,
    content: dict,
    created_at: datetime,
    note_id: str | None = None,
    finalized_at: datetime | None = None,
) -> Note:
    return Note(
        id=note_id or str(uuid.uuid4()),
        patient_id=PATIENT_ID,
        note_type=note_type,
        created_at=created_at,
        updated_at=created_at,
        finalized_at=finalized_at,
        content=content,
    )


@pytest.fixture
def notes_repo() -> InMemoryNotesRepository:
    _repo = InMemoryNotesRepository()
    _repo.grant_all_access()
    return _repo


@pytest.fixture
def patient_documents_repo() -> InMemoryPatientDocumentRepository:
    _repo = InMemoryPatientDocumentRepository()
    _repo.grant_access(PATIENT_ID, USER_ID)
    return _repo


def _make_patient_document(
    *,
    doc_id: str | None = None,
    filename: str = "intake.pdf",
    extracted_text: str | None = "Patient reports chronic insomnia.",
    created_at: datetime | None = None,
    patient_id: str = PATIENT_ID,
    user_id: str = USER_ID,
) -> PatientDocument:
    return PatientDocument(
        id=doc_id or str(uuid.uuid4()),
        patient_id=patient_id,
        user_id=user_id,
        filename=filename,
        mime_type="application/pdf",
        gcs_path=f"tenants/t/{patient_id}/{doc_id or 'doc'}.pdf",
        size_bytes=1234,
        created_at=created_at or datetime.now(UTC),
        extracted_text=extracted_text,
        finalized_at=created_at or datetime.now(UTC),
    )


@pytest.fixture
def soap_content() -> dict:
    return {
        "subjective": {
            "chief_complaint": "Sleep onset insomnia for 6 weeks",
            "mood_affect": "Anxious",
            "symptoms": ["wakeful at night", "fatigue"],
            "client_narrative": "Reports increased work stress.",
        },
        "objective": {
            "appearance": "Well-groomed",
            "behavior": "Cooperative",
            "speech": "Normal rate and rhythm",
            "thought_process": "Linear",
            "affect_observed": "Mildly anxious",
        },
        "assessment": {
            "clinical_impression": "Adjustment disorder with anxiety",
            "progress": "Modest improvement",
            "risk_assessment": "No suicidal ideation",
            "functioning_level": "Mild impairment",
        },
        "plan": {
            "interventions_used": ["CBT-I psychoeducation"],
            "homework_assignments": ["Sleep diary"],
            "next_steps": ["Stimulus control"],
            "next_session": "Weekly cadence",
        },
    }


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty_string_is_zero(self) -> None:
        assert estimate_tokens("") == 0

    def test_rounds_up(self) -> None:
        # 5 chars / 4 chars-per-token = 2 (round up)
        assert estimate_tokens("hello") == 2

    def test_uses_chars_per_token(self) -> None:
        text = "a" * (CHARS_PER_TOKEN * 100)
        assert estimate_tokens(text) == 100


# ---------------------------------------------------------------------------
# pasted_text
# ---------------------------------------------------------------------------


class TestPastedText:
    def test_emits_header_and_content(self, notes_repo: InMemoryNotesRepository) -> None:
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PASTED_TEXT: {"content": "External note body."}},
        )
        assert "USER-PASTED EXTERNAL DOCUMENT" in bundle.text
        assert "External note body." in bundle.text
        included_keys = [s["source_key"] for s in bundle.manifest["sources_included"]]
        assert SOURCE_KEY_PASTED_TEXT in included_keys

    def test_rejects_oversize_paste(self, notes_repo: InMemoryNotesRepository) -> None:
        too_big = "x" * (PASTED_TEXT_MAX_CHARS + 1)
        with pytest.raises(InvalidSelectionError):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_id=PATIENT_ID,
            user_id=USER_ID,
                selection={SOURCE_KEY_PASTED_TEXT: {"content": too_big}},
            )

    def test_rejects_missing_content_key(self, notes_repo: InMemoryNotesRepository) -> None:
        with pytest.raises(InvalidSelectionError):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_id=PATIENT_ID,
            user_id=USER_ID,
                selection={SOURCE_KEY_PASTED_TEXT: True},
            )

    def test_overflow_raises(self, notes_repo: InMemoryNotesRepository) -> None:
        # 32k chars / 4 chars-per-token = 8000 tokens. Use a small budget
        # to force overflow without bumping the per-paste cap.
        content = "a" * PASTED_TEXT_MAX_CHARS
        with pytest.raises(ContextOverflowError) as exc:
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_id=PATIENT_ID,
            user_id=USER_ID,
                selection={SOURCE_KEY_PASTED_TEXT: {"content": content}},
                token_budget=100,
            )
        assert exc.value.token_budget == 100
        assert exc.value.pasted_tokens > 100


# ---------------------------------------------------------------------------
# Progress notes — recent + explicit
# ---------------------------------------------------------------------------


class TestProgressNotesRecent:
    def test_loads_session_notes_newest_first(
        self,
        notes_repo: InMemoryNotesRepository,
        soap_content: dict,
    ) -> None:
        now = datetime.now(UTC)
        old = _make_note(
            note_type="soap",
            content=soap_content,
            created_at=now.replace(year=now.year - 1),
            finalized_at=now.replace(year=now.year - 1),
        )
        new = _make_note(
            note_type="soap",
            content=soap_content,
            created_at=now,
            finalized_at=now,
        )
        notes_repo.add(old)
        notes_repo.add(new)

        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PROGRESS_NOTES_RECENT: True},
        )
        included = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PROGRESS_NOTES_RECENT
        )
        # NotesRepository sorts finalized_at desc, so the newest finalized
        # note appears first.
        assert included["note_ids"][0] == new.id

    def test_honors_limit(
        self,
        notes_repo: InMemoryNotesRepository,
        soap_content: dict,
    ) -> None:
        base = datetime.now(UTC)
        for i in range(5):
            notes_repo.add(
                _make_note(
                    note_type="narrative",
                    content={"note": {"body": f"Visit {i}"}},
                    created_at=base.replace(microsecond=i),
                    finalized_at=base.replace(microsecond=i),
                )
            )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PROGRESS_NOTES_RECENT: {"limit": 2}},
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PROGRESS_NOTES_RECENT
        )
        assert entry["row_count"] == 2

    def test_rejects_bad_limit(self, notes_repo: InMemoryNotesRepository) -> None:
        with pytest.raises(InvalidSelectionError):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_id=PATIENT_ID,
            user_id=USER_ID,
                selection={SOURCE_KEY_PROGRESS_NOTES_RECENT: {"limit": 0}},
            )


class TestProgressNotesExplicit:
    def test_loads_only_requested_ids_in_order(
        self,
        notes_repo: InMemoryNotesRepository,
        soap_content: dict,
    ) -> None:
        base = datetime.now(UTC)
        a = _make_note(
            note_type="soap",
            content=soap_content,
            created_at=base,
            note_id="note-a",
        )
        b = _make_note(
            note_type="soap",
            content=soap_content,
            created_at=base,
            note_id="note-b",
        )
        c = _make_note(
            note_type="soap",
            content=soap_content,
            created_at=base,
            note_id="note-c",
        )
        notes_repo.add(a)
        notes_repo.add(b)
        notes_repo.add(c)
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PROGRESS_NOTES_EXPLICIT: {"note_ids": ["note-c", "note-a"]}},
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PROGRESS_NOTES_EXPLICIT
        )
        assert entry["note_ids"] == ["note-c", "note-a"]

    def test_silently_ignores_missing_ids(
        self,
        notes_repo: InMemoryNotesRepository,
        soap_content: dict,
    ) -> None:
        notes_repo.add(
            _make_note(
                note_type="soap",
                content=soap_content,
                created_at=datetime.now(UTC),
                note_id="note-real",
            )
        )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={
                SOURCE_KEY_PROGRESS_NOTES_EXPLICIT: {"note_ids": ["note-real", "note-ghost"]}
            },
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PROGRESS_NOTES_EXPLICIT
        )
        assert entry["row_count"] == 1
        assert entry["note_ids"] == ["note-real"]

    def test_rejects_non_string_ids(self, notes_repo: InMemoryNotesRepository) -> None:
        with pytest.raises(InvalidSelectionError):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_id=PATIENT_ID,
            user_id=USER_ID,
                selection={SOURCE_KEY_PROGRESS_NOTES_EXPLICIT: {"note_ids": [1, 2]}},
            )


# ---------------------------------------------------------------------------
# Patient-document sources — intake, treatment plan, safety plan, meds
# ---------------------------------------------------------------------------


class TestPatientDocumentSources:
    def test_intake_loads_most_recent(
        self,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        base = datetime.now(UTC)
        older = _make_note(
            note_type="intake",
            content={"history_of_present_illness": "Old intake"},
            created_at=base.replace(year=base.year - 1),
            finalized_at=base.replace(year=base.year - 1),
        )
        newer = _make_note(
            note_type="intake",
            content={"history_of_present_illness": "New intake content"},
            created_at=base,
            finalized_at=base,
        )
        notes_repo.add(older)
        notes_repo.add(newer)

        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_MOST_RECENT_INTAKE: True},
        )
        assert "New intake content" in bundle.text
        assert "Old intake" not in bundle.text

    def test_safety_plan_active(self, notes_repo: InMemoryNotesRepository) -> None:
        notes_repo.add(
            _make_note(
                note_type="safety_plan",
                content={"warning_signs": ["isolating from peers"]},
                created_at=datetime.now(UTC),
            )
        )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_SAFETY_PLAN_ACTIVE: True},
        )
        assert "ACTIVE SAFETY PLAN" in bundle.text
        assert "isolating from peers" in bundle.text

    def test_treatment_plan_active(self, notes_repo: InMemoryNotesRepository) -> None:
        notes_repo.add(
            _make_note(
                note_type="treatment_plan",
                content={"goals": ["reduce panic episodes"]},
                created_at=datetime.now(UTC),
            )
        )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_TREATMENT_PLAN_ACTIVE: True},
        )
        assert "ACTIVE TREATMENT PLAN" in bundle.text
        assert "reduce panic episodes" in bundle.text

    def test_current_medications(self, notes_repo: InMemoryNotesRepository) -> None:
        notes_repo.add(
            _make_note(
                note_type="medications",
                content={"active": ["Sertraline 100mg qAM"]},
                created_at=datetime.now(UTC),
            )
        )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_CURRENT_MEDICATIONS: True},
        )
        assert "CURRENT MEDICATIONS" in bundle.text
        assert "Sertraline" in bundle.text

    def test_absent_patient_document_reports_empty(
        self, notes_repo: InMemoryNotesRepository
    ) -> None:
        # No notes added at all — patient has nothing of the requested
        # types. The bundle still assembles; the manifest shows row_count=0.
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_TREATMENT_PLAN_ACTIVE: True},
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_TREATMENT_PLAN_ACTIVE
        )
        assert entry["row_count"] == 0
        assert bundle.text == ""

    def test_rejects_non_boolean_selection(self, notes_repo: InMemoryNotesRepository) -> None:
        with pytest.raises(InvalidSelectionError):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_id=PATIENT_ID,
            user_id=USER_ID,
                selection={SOURCE_KEY_SAFETY_PLAN_ACTIVE: {"limit": 1}},
            )


# ---------------------------------------------------------------------------
# Stub sources — lab + vitals (modules not yet in OSS)
# ---------------------------------------------------------------------------


class TestStubSources:
    def test_lab_values_recent_is_empty_stub(self, notes_repo: InMemoryNotesRepository) -> None:
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_LAB_VALUES_RECENT: {"limit": 5}},
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_LAB_VALUES_RECENT
        )
        assert entry["row_count"] == 0
        assert entry["reason"] == "module_not_available"

    def test_vitals_recent_is_empty_stub(self, notes_repo: InMemoryNotesRepository) -> None:
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_VITALS_RECENT: True},
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_VITALS_RECENT
        )
        assert entry["row_count"] == 0


# ---------------------------------------------------------------------------
# Selection plumbing — unknown keys, falsy values
# ---------------------------------------------------------------------------


class TestSelectionPlumbing:
    def test_rejects_unknown_key(self, notes_repo: InMemoryNotesRepository) -> None:
        with pytest.raises(InvalidSelectionError):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_id=PATIENT_ID,
            user_id=USER_ID,
                selection={"not_a_real_source": True},
            )

    def test_falsy_keys_are_skipped(self, notes_repo: InMemoryNotesRepository) -> None:
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={
                SOURCE_KEY_CURRENT_MEDICATIONS: False,
                SOURCE_KEY_SAFETY_PLAN_ACTIVE: None,
            },
        )
        assert bundle.text == ""
        assert bundle.manifest["sources_included"] == []

    def test_default_selection_is_valid(
        self,
        notes_repo: InMemoryNotesRepository,
        soap_content: dict,
    ) -> None:
        # Even with an empty chart the default selection should assemble
        # without raising — every patient-document source returns
        # row_count=0 in the manifest.
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection=default_source_selection(),
        )
        included_keys = {s["source_key"] for s in bundle.manifest["sources_included"]}
        # All seven default sources land in the manifest.
        assert included_keys == {
            SOURCE_KEY_CURRENT_MEDICATIONS,
            SOURCE_KEY_MOST_RECENT_INTAKE,
            SOURCE_KEY_PROGRESS_NOTES_RECENT,
            SOURCE_KEY_TREATMENT_PLAN_ACTIVE,
            SOURCE_KEY_SAFETY_PLAN_ACTIVE,
            SOURCE_KEY_LAB_VALUES_RECENT,
            SOURCE_KEY_VITALS_RECENT,
        }


# ---------------------------------------------------------------------------
# Token-budget enforcement + truncation policy
# ---------------------------------------------------------------------------


class TestTruncationPolicy:
    def _seed_progress_notes(
        self,
        notes_repo: InMemoryNotesRepository,
        soap_content: dict,
        count: int,
    ) -> list[str]:
        ids = []
        base = datetime.now(UTC)
        for i in range(count):
            note = _make_note(
                note_type="soap",
                content=soap_content,
                created_at=base.replace(microsecond=i),
                finalized_at=base.replace(microsecond=i),
                note_id=f"note-{i}",
            )
            notes_repo.add(note)
            ids.append(note.id)
        return ids

    def test_drops_oldest_progress_notes_first(
        self,
        notes_repo: InMemoryNotesRepository,
        soap_content: dict,
    ) -> None:
        # 5 progress notes, budget tight enough that some rows must be
        # shed but not so tight that the whole source gets dropped.
        ids = self._seed_progress_notes(notes_repo, soap_content, 5)
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PROGRESS_NOTES_RECENT: {"limit": 5}},
            token_budget=600,
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PROGRESS_NOTES_RECENT
        )
        assert "rows_dropped" in entry
        assert entry["rows_dropped"] >= 1
        assert 1 <= entry["row_count"] < 5
        # The newest note (highest microsecond) must survive; oldest goes first.
        assert ids[-1] in entry["note_ids"]

    def test_drops_low_priority_sources_before_high_priority(
        self,
        notes_repo: InMemoryNotesRepository,
        soap_content: dict,
    ) -> None:
        # Treatment plan is priority 7 (low), safety plan is priority 3
        # (high). With a budget too small for both, treatment plan goes
        # first.
        notes_repo.add(
            _make_note(
                note_type="treatment_plan",
                content={"goals": ["x" * 5000]},
                created_at=datetime.now(UTC),
                note_id="tp-1",
            )
        )
        notes_repo.add(
            _make_note(
                note_type="safety_plan",
                content={"warning_signs": ["y" * 5000]},
                created_at=datetime.now(UTC),
                note_id="sp-1",
            )
        )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={
                SOURCE_KEY_TREATMENT_PLAN_ACTIVE: True,
                SOURCE_KEY_SAFETY_PLAN_ACTIVE: True,
            },
            token_budget=2000,
        )
        dropped_keys = {s["source_key"] for s in bundle.manifest["sources_dropped"]}
        included_keys = {s["source_key"] for s in bundle.manifest["sources_included"]}
        assert SOURCE_KEY_TREATMENT_PLAN_ACTIVE in dropped_keys
        assert SOURCE_KEY_SAFETY_PLAN_ACTIVE in included_keys

    def test_below_budget_keeps_everything(
        self,
        notes_repo: InMemoryNotesRepository,
        soap_content: dict,
    ) -> None:
        self._seed_progress_notes(notes_repo, soap_content, 3)
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PROGRESS_NOTES_RECENT: {"limit": 3}},
            token_budget=DEFAULT_TOKEN_BUDGET,
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PROGRESS_NOTES_RECENT
        )
        assert entry["row_count"] == 3
        assert "rows_dropped" not in entry


# ---------------------------------------------------------------------------
# Integration — full bundle assembly with mixed sources
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_full_bundle_assembly(
        self,
        notes_repo: InMemoryNotesRepository,
        soap_content: dict,
    ) -> None:
        base = datetime.now(UTC)
        # Seed every backed source type so we exercise the full
        # serializer surface plus the priority ordering.
        notes_repo.add(
            _make_note(
                note_type="medications",
                content={"active": ["Sertraline 100mg qAM"]},
                created_at=base,
                note_id="med-1",
            )
        )
        notes_repo.add(
            _make_note(
                note_type="safety_plan",
                content={"warning_signs": ["isolation"], "coping": ["walk"]},
                created_at=base,
                note_id="sp-1",
            )
        )
        notes_repo.add(
            _make_note(
                note_type="intake",
                content={"history_of_present_illness": "Acute anxiety since job loss."},
                created_at=base,
                finalized_at=base,
                note_id="intake-1",
            )
        )
        notes_repo.add(
            _make_note(
                note_type="treatment_plan",
                content={"goals": ["sleep hygiene", "exposure plan"]},
                created_at=base,
                note_id="tp-1",
            )
        )
        for i in range(3):
            notes_repo.add(
                _make_note(
                    note_type="soap",
                    content=soap_content,
                    created_at=base.replace(microsecond=i),
                    finalized_at=base.replace(microsecond=i),
                    note_id=f"soap-{i}",
                )
            )

        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={
                SOURCE_KEY_PASTED_TEXT: {"content": "External progress note."},
                **default_source_selection(),
            },
        )

        # Priority ordering: pasted_text first, then medications, then
        # safety plan, etc. Verify the section headers appear in the
        # expected order in the rendered text.
        pasted_idx = bundle.text.index("USER-PASTED EXTERNAL DOCUMENT")
        meds_idx = bundle.text.index("CURRENT MEDICATIONS")
        safety_idx = bundle.text.index("ACTIVE SAFETY PLAN")
        intake_idx = bundle.text.index("MOST RECENT INTAKE")
        progress_idx = bundle.text.index("RECENT PROGRESS NOTES")
        treatment_idx = bundle.text.index("ACTIVE TREATMENT PLAN")
        assert pasted_idx < meds_idx < safety_idx < intake_idx < progress_idx < treatment_idx

        # Manifest is PHI-free: only ids, counts, and token estimates.
        manifest = bundle.manifest
        assert manifest["patient_id"] == PATIENT_ID
        assert manifest["total_tokens_est"] == bundle.total_tokens_est
        # No raw note content leaks into manifest entries.
        for entry in manifest["sources_included"]:
            for value in entry.values():
                if isinstance(value, str):
                    assert "Sertraline" not in value
                    assert "anxiety" not in value.lower() or value == entry.get("source_key", "")


# ---------------------------------------------------------------------------
# patient_documents source (THERAPY-ak6m.2.2)
# ---------------------------------------------------------------------------


class TestPatientDocumentsSource:
    def test_emits_section_with_filename_and_text(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        uploaded = datetime(2026, 4, 12, tzinfo=UTC)
        patient_documents_repo.add(
            _make_patient_document(
                doc_id="doc-1",
                filename="prior_psychiatry_records.pdf",
                extracted_text="History: GAD diagnosed 2024, sertraline 100mg.",
                created_at=uploaded,
            )
        )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=patient_documents_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: True},
        )
        assert "UPLOADED PATIENT DOCUMENTS" in bundle.text
        assert "prior_psychiatry_records.pdf" in bundle.text
        assert "uploaded 2026-04-12" in bundle.text
        assert "GAD diagnosed 2024" in bundle.text

    def test_requires_repo_when_selected(
        self,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        with pytest.raises(InvalidSelectionError, match="patient_documents_repo"):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_id=PATIENT_ID,
                user_id=USER_ID,
                selection={SOURCE_KEY_PATIENT_DOCUMENTS: True},
            )

    def test_rejects_non_dict_non_true_selection(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        with pytest.raises(InvalidSelectionError):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_documents_repo=patient_documents_repo,
                patient_id=PATIENT_ID,
                user_id=USER_ID,
                selection={SOURCE_KEY_PATIENT_DOCUMENTS: "all"},
            )

    def test_rejects_limit_and_document_ids_together(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        with pytest.raises(InvalidSelectionError, match="mutually exclusive"):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_documents_repo=patient_documents_repo,
                patient_id=PATIENT_ID,
                user_id=USER_ID,
                selection={
                    SOURCE_KEY_PATIENT_DOCUMENTS: {
                        "limit": 2,
                        "document_ids": ["doc-1"],
                    }
                },
            )

    def test_rejects_non_string_document_ids(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        with pytest.raises(InvalidSelectionError):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_documents_repo=patient_documents_repo,
                patient_id=PATIENT_ID,
                user_id=USER_ID,
                selection={SOURCE_KEY_PATIENT_DOCUMENTS: {"document_ids": [1, 2]}},
            )

    def test_rejects_limit_outside_bounds(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        with pytest.raises(InvalidSelectionError):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_documents_repo=patient_documents_repo,
                patient_id=PATIENT_ID,
                user_id=USER_ID,
                selection={SOURCE_KEY_PATIENT_DOCUMENTS: {"limit": 0}},
            )
        with pytest.raises(InvalidSelectionError):
            assemble_context_bundle(
                notes_repo=notes_repo,
                patient_documents_repo=patient_documents_repo,
                patient_id=PATIENT_ID,
                user_id=USER_ID,
                selection={
                    SOURCE_KEY_PATIENT_DOCUMENTS: {
                        "limit": PATIENT_DOCUMENTS_LIMIT_MAX + 1
                    }
                },
            )

    def test_limit_caps_to_most_recent(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        base = datetime(2026, 4, 1, tzinfo=UTC)
        for i in range(5):
            patient_documents_repo.add(
                _make_patient_document(
                    doc_id=f"doc-{i}",
                    filename=f"file-{i}.pdf",
                    extracted_text=f"contents of doc {i}",
                    created_at=base.replace(day=1 + i),
                )
            )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=patient_documents_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: {"limit": 2}},
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PATIENT_DOCUMENTS
        )
        # InMemoryPatientDocumentRepository returns newest first; limit=2
        # keeps doc-4 and doc-3.
        assert entry["row_count"] == 2
        assert entry["document_ids"] == ["doc-4", "doc-3"]

    def test_document_ids_preserve_order(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        base = datetime(2026, 3, 1, tzinfo=UTC)
        for i, did in enumerate(["doc-a", "doc-b", "doc-c"]):
            patient_documents_repo.add(
                _make_patient_document(
                    doc_id=did,
                    filename=f"{did}.pdf",
                    extracted_text=f"contents {did}",
                    created_at=base.replace(day=1 + i),
                )
            )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=patient_documents_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={
                SOURCE_KEY_PATIENT_DOCUMENTS: {"document_ids": ["doc-c", "doc-a"]}
            },
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PATIENT_DOCUMENTS
        )
        assert entry["document_ids"] == ["doc-c", "doc-a"]
        # Order in rendered text matches the caller-supplied order.
        assert bundle.text.index("doc-c.pdf") < bundle.text.index("doc-a.pdf")

    def test_skips_scanned_pdfs_without_text(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        base = datetime(2026, 4, 1, tzinfo=UTC)
        patient_documents_repo.add(
            _make_patient_document(
                doc_id="doc-text",
                filename="text.pdf",
                extracted_text="native text body",
                created_at=base.replace(day=2),
            )
        )
        patient_documents_repo.add(
            _make_patient_document(
                doc_id="doc-scan",
                filename="scan.pdf",
                extracted_text=None,
                created_at=base.replace(day=1),
            )
        )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=patient_documents_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: True},
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PATIENT_DOCUMENTS
        )
        assert entry["row_count"] == 1
        assert entry["document_ids"] == ["doc-text"]
        assert entry["skipped_no_text"] == 1
        assert "scan.pdf" not in bundle.text
        assert "text.pdf" in bundle.text

    def test_manifest_omits_extracted_text(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        secret = "PRIVILEGED CONSULT NARRATIVE 7f3c"
        patient_documents_repo.add(
            _make_patient_document(
                doc_id="doc-1",
                filename="consult.pdf",
                extracted_text=secret,
            )
        )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=patient_documents_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: True},
        )
        # Extracted text shows up in the LLM-facing bundle, never in the
        # PHI-free manifest.
        assert secret in bundle.text
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PATIENT_DOCUMENTS
        )
        for value in entry.values():
            assert not isinstance(value, str) or secret not in value

    def test_user_without_grant_sees_no_documents(
        self,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        repo = InMemoryPatientDocumentRepository()
        # Deliberately omit grant_access — caller has no read on the patient.
        repo.add(
            _make_patient_document(
                doc_id="doc-1",
                filename="prior.pdf",
                extracted_text="confidential history",
            )
        )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=repo,
            patient_id=PATIENT_ID,
            user_id="stranger-user",
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: True},
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PATIENT_DOCUMENTS
        )
        assert entry["row_count"] == 0
        assert "confidential history" not in bundle.text

    def test_truncation_drops_rows_before_whole_source(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        base = datetime(2026, 4, 1, tzinfo=UTC)
        body = "x" * 1600  # ~400 tokens per doc with the 4-char heuristic
        ids = []
        for i in range(4):
            did = f"doc-{i}"
            ids.append(did)
            patient_documents_repo.add(
                _make_patient_document(
                    doc_id=did,
                    filename=f"{did}.pdf",
                    extracted_text=body,
                    created_at=base.replace(day=1 + i),
                )
            )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=patient_documents_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: {"limit": 4}},
            token_budget=800,
        )
        entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PATIENT_DOCUMENTS
        )
        assert entry["row_count"] >= 1
        assert entry["row_count"] < 4
        assert entry["rows_dropped"] >= 1
        assert "dropped_document_ids" in entry
        # Newest doc must survive (oldest dropped first).
        assert "doc-3" in entry["document_ids"]

    def test_single_oversized_document_drops_whole_source(
        self,
        notes_repo: InMemoryNotesRepository,
        patient_documents_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        patient_documents_repo.add(
            _make_patient_document(
                doc_id="huge",
                filename="huge.pdf",
                extracted_text="z" * 20_000,
            )
        )
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=patient_documents_repo,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: True},
            token_budget=500,
        )
        dropped_keys = {s["source_key"] for s in bundle.manifest["sources_dropped"]}
        assert SOURCE_KEY_PATIENT_DOCUMENTS in dropped_keys
        assert "huge.pdf" not in bundle.text
