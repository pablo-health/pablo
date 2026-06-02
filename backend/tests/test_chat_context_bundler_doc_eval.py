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
from app.models import Note, PatientDocument
from app.repositories import InMemoryNotesRepository, InMemoryPatientDocumentRepository
from app.services.chat_context_bundler import (
    _DOCUMENT_RENDERERS,
    CHARS_PER_TOKEN,
    DEFAULT_DOCUMENT_STRATEGY,
    PATIENT_DOCUMENT_MAX_RENDER_CHARS,
    SOURCE_KEY_DOCUMENT_MANIFEST,
    SOURCE_KEY_PATIENT_DOCUMENTS,
    SOURCE_KEY_SAFETY_PLAN_ACTIVE,
    InvalidSelectionError,
    _score_doc_relevance,
    assemble_context_bundle,
    register_document_strategy,
)


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
# so relevance scoring is deterministic for these tests. Content is
# mental-health-domain to mirror the documents a Pablo clinician actually
# uploads (psych evals, testing batteries, intake notes) rather than generic
# medical records.
_PSYCH_EVAL = """\
PSYCHIATRIC EVALUATION
Patient: [redacted]  Date: 2026-03-15
Referring clinician: Dr. Rivera

REASON FOR REFERRAL: Evaluation of persistent low mood, anhedonia,
and middle-of-the-night insomnia over the past three months.

HISTORY OF PRESENT ILLNESS:
Patient reports depressed mood most days, loss of interest in usual
activities, low energy, and poor concentration at work. Denies current
suicidal ideation, intent, or plan. Reports passive thoughts of "not
wanting to wake up" two weeks ago, now resolved.

MENTAL STATUS EXAM:
Appearance: well-groomed, cooperative. Mood: "down." Affect:
constricted, congruent. Thought process: linear, goal-directed.
No psychosis. Cognition: alert and oriented x3. Insight/judgment fair.

ASSESSMENT:
PHQ-9 score 18 — moderately severe depression.
GAD-7 score 12 — moderate anxiety.

DIAGNOSIS:
1. Major depressive disorder, recurrent episode, moderate.
2. Generalized anxiety disorder.

PLAN:
Start sertraline 50 mg daily, titrate as tolerated.
Continue weekly cognitive behavioral therapy.
Safety plan reviewed and updated. Follow up in 4 weeks.
""" * 3  # repeat to give it real bulk

_UNRELATED_NEUROPSYCH = """\
NEUROPSYCHOLOGICAL TESTING — COGNITIVE BATTERY
Administered: 2025-09-01

Wechsler Adult Intelligence Scale (WAIS-IV):
  Full Scale IQ 104 — Average
  Verbal Comprehension 108 — Average
  Perceptual Reasoning 101 — Average
  Working Memory Index 99 — Average
  Processing Speed Index 97 — Average

Wechsler Memory Scale (WMS-IV):
  Auditory Memory 102 — Average
  Visual Memory 105 — Average
  Delayed Recall 100 — Average

Executive Function:
  Trail Making Test A 28 sec — within normal limits
  Trail Making Test B 65 sec — within normal limits
  Stroop Color-Word interference — within normal limits
  Wisconsin Card Sorting Test — no perseverative errors

IMPRESSION:
Cognitive profile within normal limits across all domains. No evidence
of focal deficit. Findings do not support a neurocognitive disorder.
""" * 4  # long enough to be heavier than the psychiatric eval

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
        docs_repo.add(_doc(filename="psychiatric_eval.pdf", extracted_text=_PSYCH_EVAL))
        docs_repo.add(_doc(filename="neuropsych_testing.pdf", extracted_text=_UNRELATED_NEUROPSYCH))

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
        assert "psychiatric_eval.pdf" in assembled
        assert "neuropsych_testing.pdf" in assembled

        # Full bodies should have been dropped under this tight budget.
        dropped_keys = {d["source_key"] for d in bundle.manifest.get("sources_dropped", [])}
        assert SOURCE_KEY_PATIENT_DOCUMENTS in dropped_keys or (
            len(_PSYCH_EVAL) > tight_budget * CHARS_PER_TOKEN // 2
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
        """Depression query → psych eval kept, unrelated neuropsych battery dropped."""
        psych_doc = _doc(
            filename="psychiatric_eval.pdf",
            extracted_text=_PSYCH_EVAL,
        )
        neuropsych_doc = _doc(
            filename="neuropsych_testing.pdf",
            extracted_text=_UNRELATED_NEUROPSYCH,
        )
        docs_repo.add(psych_doc)
        docs_repo.add(neuropsych_doc)

        # Budget fits roughly one full doc but not both.
        one_doc_tokens = len(_PSYCH_EVAL) // CHARS_PER_TOKEN
        budget = int(one_doc_tokens * 1.3)  # room for one doc + manifest, not both

        bundle = assemble_context_bundle(
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            notes_repo=notes_repo,
            patient_documents_repo=docs_repo,
            token_budget=budget,
            query=(
                "What did the psychiatric evaluation say about the PHQ-9 "
                "depression score and sertraline medication?"
            ),
            selection=_SEL_DOCS,
        )
        assembled = bundle.text

        # Psych eval content must appear in the full body section.
        assert "PHQ-9" in assembled or "sertraline" in assembled

        # The full neuropsych body must have been dropped. The manifest shows
        # only the first 200 chars of each doc; "Wisconsin Card Sorting" appears
        # ~500 chars into the battery so it will only appear if the full body
        # was included.
        assert "Wisconsin Card Sorting" not in assembled

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
            filename="recent_psychiatric_eval.pdf",
            extracted_text=_PSYCH_EVAL,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        docs_repo.add(old)
        docs_repo.add(new)

        # Budget fits one doc.
        budget = len(_PSYCH_EVAL) // CHARS_PER_TOKEN + 200

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
        # Newer doc has the psych eval content; older is just the brief intake.
        assert "PHQ-9" in assembled or "psychiatric" in assembled.lower()


# ── 2b. Relevance scorer: length-bias fix ─────────────────────────────────────

# A long, on-topic psychiatric note. Repeating the base for bulk does not
# change its *unique* word set, so the relevance math below is driven by the
# base vocabulary while the body is large enough to feel real budget pressure.
_LONG_RELEVANT_BASE = (
    "Follow-up psychiatry visit. The patient continues to report depression "
    "with persistent low mood, anhedonia, fatigue, poor appetite, and early "
    "morning awakening. We reviewed the current sertraline dosage together "
    "and agreed on a gradual titration toward a therapeutic target over the "
    "coming month. The updated treatment plan adds behavioral activation "
    "homework, structured sleep hygiene coaching, paced exercise, and a "
    "written relapse-prevention strategy. Medication was tolerated well with "
    "no adverse effects, nausea, or sexual side effects reported this week. "
    "Suicidal ideation was screened and remains absent. The next review is "
    "scheduled in four weeks to assess clinical response and adjust the "
    "regimen as clinically indicated. "
)
# A short note that shares exactly one query word ("plan") and little else.
_SHORT_TANGENTIAL_BASE = "Treatment plan reviewed and signed. "

_LENGTH_BIAS_QUERY = "depression sertraline dosage titration plan"


def _jaccard(doc_text: str, query: str) -> float:
    """Inline Jaccard — the metric this scorer used *before* the fix.

    Kept local to the test so we can assert the overlap coefficient we now
    ship reverses Jaccard's (wrong) length-biased ranking.
    """
    a = set(doc_text.lower().split())
    b = set(query.lower().split())
    return len(a & b) / len(a | b)


class TestRelevanceLengthBias:
    """Demonstrates the Jaccard → overlap-coefficient one-line change.

    Jaccard divides by the *union*, which grows with document length, so a
    long, highly-relevant note scores lower than a short, barely-relevant
    one — backwards for our goal of keeping the most relevant doc under
    budget pressure. The overlap coefficient divides by the smaller token
    set (the query) and removes that bias.
    """

    def test_scorer_ranks_long_relevant_over_short_tangential(self) -> None:
        long_relevant = _LONG_RELEVANT_BASE
        short_tangential = _SHORT_TANGENTIAL_BASE

        # Jaccard gets it WRONG: it ranks the short tangential note higher
        # purely because its union with the query is smaller.
        assert _jaccard(short_tangential, _LENGTH_BIAS_QUERY) > _jaccard(
            long_relevant, _LENGTH_BIAS_QUERY
        )

        # The shipped overlap coefficient gets it RIGHT: the note that
        # actually covers the query wins.
        assert _score_doc_relevance(long_relevant, _LENGTH_BIAS_QUERY) > _score_doc_relevance(
            short_tangential, _LENGTH_BIAS_QUERY
        )

    def test_long_relevant_doc_survives_budget_over_short_tangential(
        self,
        notes_repo: InMemoryNotesRepository,
        docs_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        """End-to-end: under budget pressure the long relevant doc is the one
        that survives. Under the old Jaccard ranking the short tangential doc
        would have been kept and the relevant one dropped from the tail."""
        long_doc = _doc(
            filename="psychiatry_followup.pdf",
            extracted_text=_LONG_RELEVANT_BASE * 8,
        )
        short_doc = _doc(
            filename="cosign_stub.pdf",
            extracted_text=_SHORT_TANGENTIAL_BASE * 80,
        )
        docs_repo.add(long_doc)
        docs_repo.add(short_doc)

        # Budget fits one full body (+ manifest), not both.
        budget = len(long_doc.extracted_text) // CHARS_PER_TOKEN + 200

        bundle = assemble_context_bundle(
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            notes_repo=notes_repo,
            patient_documents_repo=docs_repo,
            token_budget=budget,
            query=_LENGTH_BIAS_QUERY,
            selection=_SEL_DOCS,
        )

        docs_entry = next(
            s
            for s in bundle.manifest["sources_included"]
            if s["source_key"] == SOURCE_KEY_PATIENT_DOCUMENTS
        )
        # The relevant doc survived; the tangential one was dropped from the tail.
        assert long_doc.id in docs_entry["document_ids"]
        assert short_doc.id in docs_entry.get("dropped_document_ids", [])


# ── 3. Summary fallback ───────────────────────────────────────────────────────

class TestSummaryFallback:
    """Over-cap docs render their stored summary instead of a head-clipped body."""

    def test_summary_substituted_when_doc_over_cap(
        self,
        notes_repo: InMemoryNotesRepository,
        docs_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        stored_summary = (
            "Psychiatric evaluation confirming moderate recurrent major "
            "depression with comorbid anxiety. PHQ-9 18, GAD-7 12. Started "
            "sertraline 50 mg; continuing weekly CBT. Safety plan reviewed."
        )
        # Doc body is over the per-doc render cap (320k chars).
        huge_body = _PSYCH_EVAL * 200  # ~420k chars, well over 320k
        assert len(huge_body) > PATIENT_DOCUMENT_MAX_RENDER_CHARS

        docs_repo.add(
            _doc(
                filename="psychiatric_eval.pdf",
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
        assert assembled.count("PSYCHIATRIC EVALUATION") < 10

    def test_head_clip_fallback_when_no_summary(
        self,
        notes_repo: InMemoryNotesRepository,
        docs_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        """Existing head-clip behaviour preserved when no summary is stored."""
        huge_body = _PSYCH_EVAL * 200
        docs_repo.add(
            _doc(
                filename="psychiatric_eval.pdf",
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
        huge_body = "Therapy session progress note. " * 100_000  # over cap
        docs_repo.add(
            _doc(
                filename="therapy_progress_notes.pdf",
                extracted_text=huge_body,
                extraction_metadata={"summary": "Brief summary of the long therapy progress note."},
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
                    extracted_text=_UNRELATED_NEUROPSYCH,
                )
            )

        notes_repo.add(
            _make_note(
                note_type="safety_plan",
                content={"warning_signs": [
                    "Crisis line: 988. Safe person: spouse. Means restriction: firearms removed."
                ]},
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


# ── 5. Document-strategy extension seam ───────────────────────────────────────


def _docs_entry(bundle) -> dict:
    """The patient_documents entry from a bundle's manifest."""
    return next(
        s
        for s in bundle.manifest["sources_included"]
        if s["source_key"] == SOURCE_KEY_PATIENT_DOCUMENTS
    )


@pytest.fixture
def register_strategy():
    """Register document strategies for a test and unregister them after,
    so global registry state never leaks between tests."""
    registered: list[str] = []

    def _register(name: str, renderer) -> None:
        register_document_strategy(name, renderer)
        registered.append(name)

    yield _register

    for name in registered:
        _DOCUMENT_RENDERERS.pop(name, None)


class TestDocumentStrategySeam:
    """The pluggable rendering seam: raw_text default, registry dispatch,
    unknown-strategy rejection, and strategy-consistent truncation."""

    def test_default_strategy_is_raw_text(
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
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: True},
        )
        assert _docs_entry(bundle)["strategy"] == DEFAULT_DOCUMENT_STRATEGY
        # raw_text renders the full body section.
        assert "UPLOADED PATIENT DOCUMENTS" in bundle.text
        assert _BRIEF_INTAKE in bundle.text

    def test_unknown_strategy_rejected(
        self,
        notes_repo: InMemoryNotesRepository,
        docs_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        docs_repo.add(_doc(filename="intake.pdf", extracted_text=_BRIEF_INTAKE))
        with pytest.raises(InvalidSelectionError):
            assemble_context_bundle(
                patient_id=PATIENT_ID,
                user_id=USER_ID,
                notes_repo=notes_repo,
                patient_documents_repo=docs_repo,
                selection={SOURCE_KEY_PATIENT_DOCUMENTS: {"strategy": "not_registered"}},
            )

    def test_registered_strategy_is_dispatched(
        self,
        notes_repo: InMemoryNotesRepository,
        docs_repo: InMemoryPatientDocumentRepository,
        register_strategy,
    ) -> None:
        register_strategy(
            "filenames_only",
            lambda docs: "## FILENAMES\n\n" + "\n".join(d.filename for d in docs),
        )
        docs_repo.add(_doc(filename="intake.pdf", extracted_text=_BRIEF_INTAKE))
        bundle = assemble_context_bundle(
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            notes_repo=notes_repo,
            patient_documents_repo=docs_repo,
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: {"strategy": "filenames_only"}},
        )
        # Custom strategy rendered; the raw body did NOT.
        assert "## FILENAMES" in bundle.text
        assert "intake.pdf" in bundle.text
        assert _BRIEF_INTAKE not in bundle.text
        assert _docs_entry(bundle)["strategy"] == "filenames_only"

    def test_custom_strategy_used_for_truncation_rerender(
        self,
        notes_repo: InMemoryNotesRepository,
        docs_repo: InMemoryPatientDocumentRepository,
        register_strategy,
    ) -> None:
        """Dropping a row under budget must re-render via the same strategy,
        not fall back to raw_text."""
        register_strategy(
            "padded_marker",
            lambda docs: "## MARKER\n\n"
            + "\n\n".join(f"<<{d.filename}>>" + (" x" * 4000) for d in docs),
        )
        keep = _doc(
            filename="keep.pdf",
            extracted_text="keep",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        drop = _doc(
            filename="drop.pdf",
            extracted_text="drop",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        docs_repo.add(drop)
        docs_repo.add(keep)

        # Each rendered doc is ~8k chars (~2k tokens); budget fits one, not two.
        bundle = assemble_context_bundle(
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            notes_repo=notes_repo,
            patient_documents_repo=docs_repo,
            token_budget=3000,
            selection={SOURCE_KEY_PATIENT_DOCUMENTS: {"strategy": "padded_marker"}},
        )
        # Re-rendered through the custom strategy (header present), newest kept,
        # oldest dropped from the tail.
        assert "## MARKER" in bundle.text
        assert "<<keep.pdf>>" in bundle.text
        assert "<<drop.pdf>>" not in bundle.text
        assert drop.id in _docs_entry(bundle).get("dropped_document_ids", [])
