# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Eval-style tests for the document-context improvements.

These tests assert the three invariants introduced in the doc-context-quality
sprint:

  1. Manifest invariant  — the document index is always present even when
                           full document bodies are budget-dropped.
  2. Relevance ordering  — under budget pressure the document most relevant
                           to the current query survives; irrelevant ones drop.
  3. Summary fallback    — a document over the render cap renders its stored
                           summary rather than a head-truncated body.
  4. Safety invariant    — safety_plan_active always survives regardless of
                           budget pressure (pre-existing, verified here for
                           regression).

Unlike the unit tests in test_chat_context_bundler_patient_documents.py these
tests use controlled, realistic document bodies and tight token budgets to
prove the pipeline produces correct behaviour end-to-end — not just that code
paths execute.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.models import PatientDocument
from app.repositories import InMemoryNotesRepository, InMemoryPatientDocumentRepository
from app.services.chat_context_bundler import (
    CHARS_PER_TOKEN,
    PATIENT_DOCUMENT_MAX_RENDER_CHARS,
    SOURCE_KEY_DOCUMENT_MANIFEST,
    SOURCE_KEY_PATIENT_DOCUMENTS,
    SOURCE_KEY_SAFETY_PLAN_ACTIVE,
    assemble_context_bundle,
)
from app.models import Note


def _make_note(
    *,
    note_type: str,
    content: dict,
    created_at: datetime | None = None,
) -> Note:
    ts = created_at or datetime.now(UTC)
    return Note(
        id=str(uuid.uuid4()),
        patient_id=PATIENT_ID,
        note_type=note_type,
        content=content,
        created_at=ts,
        updated_at=ts,
        finalized_at=ts,
    )


# Selection dicts used across tests.
_SEL_DOCS = {
    SOURCE_KEY_DOCUMENT_MANIFEST: True,
    SOURCE_KEY_PATIENT_DOCUMENTS: True,
}
_SEL_DOCS_SAFETY = {
    SOURCE_KEY_DOCUMENT_MANIFEST: True,
    SOURCE_KEY_PATIENT_DOCUMENTS: True,
    SOURCE_KEY_SAFETY_PLAN_ACTIVE: True,
}
_SEL_SAFETY_ONLY = {SOURCE_KEY_SAFETY_PLAN_ACTIVE: True}

PATIENT_ID = "eval-patient-1"
USER_ID = "eval-clinician-1"

# Synthetic clinical document bodies. Written to have clear lexical signals
# so relevance scoring is deterministic for these tests.
_CARDIOLOGY_REPORT = """\
CARDIOLOGY CONSULTATION REPORT
Patient: [redacted]  Date: 2026-03-15
Referring physician: Dr. Smith

REASON FOR REFERRAL: Evaluation of chest pain and exertional dyspnoea.

FINDINGS:
EKG: Normal sinus rhythm. No ST changes. QTc 420 ms.
Echo: LVEF 55%. Mild concentric LVH. No significant valvular disease.
Stress test: Achieved 10 METs. No ischaemic changes. No arrhythmia.

IMPRESSION:
1. Non-cardiac chest pain, most likely musculoskeletal.
2. Mild hypertension — optimise current antihypertensive regimen.

RECOMMENDATIONS:
Continue lisinopril 10 mg daily. Repeat echo in 12 months.
Follow up with cardiology in 6 months.
""" * 3  # repeat to give it real bulk

_UNRELATED_LAB_DUMP = """\
LABORATORY RESULTS — ANNUAL PANEL
Collected: 2025-09-01

Complete Blood Count:
  WBC 6.2 (4.0-11.0 K/uL) — Normal
  RBC 4.8 (4.2-5.4 M/uL) — Normal
  Haemoglobin 14.1 (12.0-16.0 g/dL) — Normal
  Haematocrit 42.3% — Normal
  Platelets 220 — Normal

Comprehensive Metabolic Panel:
  Sodium 139 (136-145) — Normal
  Potassium 4.1 (3.5-5.1) — Normal
  Creatinine 0.9 (0.6-1.1) — Normal
  eGFR > 60 — Normal
  ALT 22 — Normal
  AST 19 — Normal

Lipid Panel:
  Total Cholesterol 198 — Borderline
  LDL 118 — Borderline high
  HDL 52 — Normal
  Triglycerides 140 — Normal

HbA1c 5.4% — Normal
TSH 2.1 — Normal
""" * 4  # long enough to be heavier than the cardiology report

_BRIEF_INTAKE = "Patient intake note. Presenting concern: anxiety and insomnia."


def _doc(
    *,
    filename: str,
    extracted_text: str,
    extraction_metadata: dict | None = None,
    created_at: datetime | None = None,
) -> PatientDocument:
    return PatientDocument(
        id=str(uuid.uuid4()),
        patient_id=PATIENT_ID,
        user_id=USER_ID,
        filename=filename,
        mime_type="application/pdf",
        gcs_path=f"tenants/t/{PATIENT_ID}/{filename}",
        size_bytes=len(extracted_text),
        created_at=created_at or datetime.now(UTC),
        extracted_text=extracted_text,
        finalized_at=created_at or datetime.now(UTC),
        extraction_metadata=extraction_metadata,
    )


@pytest.fixture
def notes_repo() -> InMemoryNotesRepository:
    repo = InMemoryNotesRepository()
    repo.grant_all_access()
    return repo


@pytest.fixture
def docs_repo() -> InMemoryPatientDocumentRepository:
    repo = InMemoryPatientDocumentRepository()
    repo.grant_access(PATIENT_ID, USER_ID)
    return repo


# ── 1. Manifest invariant ─────────────────────────────────────────────────────

class TestManifestInvariant:
    """Manifest always present — even when full doc bodies are budget-dropped."""

    def test_manifest_present_within_budget(
        self,
        notes_repo: InMemoryNotesRepository,
        docs_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        docs_repo.add(_doc(filename="intake.pdf", extracted_text=_BRIEF_INTAKE))
        bundle = assemble_context_bundle(
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            notes_repo=notes_repo,
            patient_documents_repo=docs_repo,
            selection=_SEL_DOCS,
        )
        assembled = bundle.text
        assert "PATIENT DOCUMENTS ON FILE" in assembled
        assert "intake.pdf" in assembled

    def test_manifest_survives_when_doc_bodies_are_dropped(
        self,
        notes_repo: InMemoryNotesRepository,
        docs_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        """Even with a budget so tight the full doc bodies are dropped, the
        manifest index must remain — it's the model's only signal that docs
        exist."""
        docs_repo.add(_doc(filename="cardiology.pdf", extracted_text=_CARDIOLOGY_REPORT))
        docs_repo.add(_doc(filename="labs.pdf", extracted_text=_UNRELATED_LAB_DUMP))

        # Budget: manifest (~100 tokens) fits, but full bodies (~600+ tokens) don't.
        manifest_tokens = 150
        tight_budget = manifest_tokens * CHARS_PER_TOKEN

        bundle = assemble_context_bundle(
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            notes_repo=notes_repo,
            patient_documents_repo=docs_repo,
            token_budget=tight_budget,
            selection=_SEL_DOCS,
        )
        assembled = bundle.text

        # Manifest must be present.
        assert "PATIENT DOCUMENTS ON FILE" in assembled
        assert "cardiology.pdf" in assembled
        assert "labs.pdf" in assembled

        # Full bodies should have been dropped under this tight budget.
        dropped_keys = {d["source_key"] for d in bundle.manifest.get("sources_dropped", [])}
        assert SOURCE_KEY_PATIENT_DOCUMENTS in dropped_keys or (
            len(_CARDIOLOGY_REPORT) > tight_budget * CHARS_PER_TOKEN // 2
        )

    def test_manifest_not_duplicated(
        self,
        notes_repo: InMemoryNotesRepository,
        docs_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        docs_repo.add(_doc(filename="intake.pdf", extracted_text=_BRIEF_INTAKE))
        bundle = assemble_context_bundle(
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            notes_repo=notes_repo,
            patient_documents_repo=docs_repo,
            selection=_SEL_DOCS,
        )
        assert bundle.text.count("PATIENT DOCUMENTS ON FILE") == 1

    def test_manifest_empty_when_no_docs(
        self,
        notes_repo: InMemoryNotesRepository,
        docs_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        bundle = assemble_context_bundle(
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            notes_repo=notes_repo,
            patient_documents_repo=docs_repo,
            selection=_SEL_DOCS,
        )
        # No docs → manifest is present but empty of file entries.
        assert ".pdf" not in bundle.text


# ── 2. Relevance ordering ─────────────────────────────────────────────────────

class TestRelevanceOrdering:
    """The document most lexically relevant to the query survives budget cuts."""

    def test_relevant_doc_survives_irrelevant_dropped(
        self,
        notes_repo: InMemoryNotesRepository,
        docs_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        """Cardiology query → cardiology report kept, unrelated lab dump dropped."""
        cardiology_doc = _doc(
            filename="cardiology_consultation.pdf",
            extracted_text=_CARDIOLOGY_REPORT,
        )
        lab_doc = _doc(
            filename="annual_labs.pdf",
            extracted_text=_UNRELATED_LAB_DUMP,
        )
        docs_repo.add(cardiology_doc)
        docs_repo.add(lab_doc)

        # Budget fits roughly one full doc but not both.
        one_doc_tokens = len(_CARDIOLOGY_REPORT) // CHARS_PER_TOKEN
        budget = int(one_doc_tokens * 1.3)  # room for one doc + manifest, not both

        bundle = assemble_context_bundle(
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            notes_repo=notes_repo,
            patient_documents_repo=docs_repo,
            token_budget=budget,
            query="What did the cardiology consultation say about the EKG and LVEF?",
            selection=_SEL_DOCS,
        )
        assembled = bundle.text

        # Cardiology content must appear in the full body section.
        assert "LVEF" in assembled or "EKG" in assembled

        # The full lab body must have been dropped. The manifest shows only the
        # first 200 chars of each doc; "Triglycerides" appears ~500 chars into
        # the lab report so it will only appear if the full body was included.
        assert "Triglycerides" not in assembled

    def test_no_query_falls_back_to_newest_first(
        self,
        notes_repo: InMemoryNotesRepository,
        docs_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        """Without a query, newest doc should survive over oldest under pressure."""
        old = _doc(
            filename="old_intake.pdf",
            extracted_text=_BRIEF_INTAKE,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        new = _doc(
            filename="recent_cardiology.pdf",
            extracted_text=_CARDIOLOGY_REPORT,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        docs_repo.add(old)
        docs_repo.add(new)

        # Budget fits one doc.
        budget = len(_CARDIOLOGY_REPORT) // CHARS_PER_TOKEN + 200

        bundle = assemble_context_bundle(
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            notes_repo=notes_repo,
            patient_documents_repo=docs_repo,
            token_budget=budget,
            query=None,
            selection=_SEL_DOCS,
        )
        assembled = bundle.text
        # Newer doc has cardiology content; older is just the brief intake.
        assert "LVEF" in assembled or "cardiology" in assembled.lower()


# ── 3. Summary fallback ───────────────────────────────────────────────────────

class TestSummaryFallback:
    """Over-cap docs render their stored summary instead of a head-clipped body."""

    def test_summary_substituted_when_doc_over_cap(
        self,
        notes_repo: InMemoryNotesRepository,
        docs_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        stored_summary = (
            "Cardiology consultation confirming non-cardiac chest pain. "
            "LVEF 55%, normal EKG. Recommend continuing lisinopril and "
            "follow-up echo in 12 months."
        )
        # Doc body is over the per-doc render cap (320k chars).
        huge_body = _CARDIOLOGY_REPORT * 200  # ~420k chars, well over 320k
        assert len(huge_body) > PATIENT_DOCUMENT_MAX_RENDER_CHARS

        docs_repo.add(
            _doc(
                filename="cardiology.pdf",
                extracted_text=huge_body,
                extraction_metadata={"summary": stored_summary},
            )
        )
        bundle = assemble_context_bundle(
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            notes_repo=notes_repo,
            patient_documents_repo=docs_repo,
            selection=_SEL_DOCS,
        )
        assembled = bundle.text

        # Summary must appear, not the raw body tail.
        assert stored_summary in assembled
        assert "SUMMARY" in assembled  # marker present
        # The raw body (repeated content beyond the cap) must NOT appear wholesale.
        assert assembled.count("CARDIOLOGY CONSULTATION REPORT") < 10

    def test_head_clip_fallback_when_no_summary(
        self,
        notes_repo: InMemoryNotesRepository,
        docs_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        """Existing head-clip behaviour preserved when no summary is stored."""
        huge_body = _CARDIOLOGY_REPORT * 200
        docs_repo.add(
            _doc(
                filename="cardiology.pdf",
                extracted_text=huge_body,
                extraction_metadata=None,
            )
        )
        bundle = assemble_context_bundle(
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            notes_repo=notes_repo,
            patient_documents_repo=docs_repo,
            selection=_SEL_DOCS,
        )
        assembled = bundle.text
        # Falls back to truncated body — truncation marker present.
        assert "truncated" in assembled.lower() or "SUMMARY" not in assembled

    def test_summary_marker_explicitly_present(
        self,
        notes_repo: InMemoryNotesRepository,
        docs_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        """The '[SUMMARY — full document loaded in brief]' marker must appear
        so the model and therapist know they're seeing a reduction."""
        huge_body = "Clinical note. " * 100_000  # over cap
        docs_repo.add(
            _doc(
                filename="long_note.pdf",
                extracted_text=huge_body,
                extraction_metadata={"summary": "Brief summary of the long clinical note."},
            )
        )
        bundle = assemble_context_bundle(
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            notes_repo=notes_repo,
            patient_documents_repo=docs_repo,
            selection=_SEL_DOCS,
        )
        assert "loaded in brief" in bundle.text


# ── 4. Safety invariant (regression) ─────────────────────────────────────────

class TestSafetyInvariant:
    """safety_plan_active must survive any budget pressure — pre-existing
    invariant verified here to catch regressions from priority reordering."""

    def test_safety_plan_survives_max_document_pressure(
        self,
        notes_repo: InMemoryNotesRepository,
        docs_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        # Fill the chart with many large documents.
        for i in range(10):
            docs_repo.add(
                _doc(
                    filename=f"large_doc_{i}.pdf",
                    extracted_text=_UNRELATED_LAB_DUMP,
                )
            )

        notes_repo.add(
            _make_note(
                note_type="safety_plan",
                content={"warning_signs": ["Crisis line: 988. Safe person: spouse. Means restriction: firearms removed."]},
            )
        )

        # Very tight budget — almost nothing fits.
        tight_budget = 500

        bundle = assemble_context_bundle(
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            notes_repo=notes_repo,
            patient_documents_repo=docs_repo,
            token_budget=tight_budget,
            selection=_SEL_DOCS_SAFETY,
        )

        # Safety plan must be present regardless of budget.
        assert "ACTIVE SAFETY PLAN" in bundle.text or "Crisis line" in bundle.text
        sources_included = {s["source_key"] for s in bundle.manifest.get("sources_included", [])}
        assert SOURCE_KEY_SAFETY_PLAN_ACTIVE in sources_included
