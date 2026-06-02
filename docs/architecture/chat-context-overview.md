# Chat Context Management — Overview

> Orientation doc for how Pablo assembles the context it sends to the model
> on each chat turn. This is the **map**; the canonical spec (source keys,
> API surface, audit policy, priority table) is
> [`patient-context-chat-oss.md`](./patient-context-chat-oss.md) — defer to
> it for exact contracts, and keep this overview in sync when it changes.

## The problem

A clinician's chat turn needs the model to "know the chart" — intake,
progress notes, an active treatment or safety plan, uploaded documents —
without blowing the context window or leaking the budget the response
itself needs. The chart can be arbitrarily large (a 200-page intake PDF);
the budget is finite. So context assembly is fundamentally a **selection
and truncation** problem: decide what goes in, in what order, and what to
shed first when it doesn't fit.

Two pieces work together:

1. **Prior-turn history** — the running transcript of the conversation.
2. **The context bundle** — a snapshot of the relevant chart material,
   rebuilt fresh on every turn.

## Prior-turn history (windowing)

The turn service composes the bundle with the system prompt and the prior
turns of the conversation. Long conversations are **windowed** rather than
sent whole: `ChatRepository.list_messages_windowed` keeps a small head
(the first K turns, currently K=2) plus a larger tail (the most recent N
turns, currently N=30). The head preserves the framing of the
conversation; the tail preserves recency. Everything between is dropped —
the bundle (below) is where durable chart facts live, so the middle of an
old transcript is the cheapest thing to lose.

## The context bundle

`assemble_context_bundle` (`backend/app/services/chat_context_bundler.py`)
is a **pure, stateless, repository-driven function** over a patient's
chart. Same code runs against the in-memory test repo and the Postgres
repo. Given a typed source selection it produces a `ContextBundle`:
the assembled prompt text plus a PHI-free `ContextManifest` that records
what actually made it in (for persistence on the user-turn row and the
audit digest).

### The pipeline

```
selection ─▶ load sources ─▶ relevance-order docs ─▶ per-doc render cap
                                                            │
        manifest + text ◀─ enforce token budget ◀──────────┘
```

1. **Load.** Each selected source key is loaded by its loader into a
   `LoadedSource` (`key`, `priority`, `rows`, rendered `text`,
   `tokens_est`, `truncatable`). Sources selected-but-empty report
   `row_count=0` rather than failing.

2. **Relevance-order documents.** When the turn carries a query, uploaded
   documents are sorted most-relevant-first by `_score_doc_relevance`
   (see below). With no query, order falls back to newest-first. This
   matters because the budget walk drops from the **tail** — so ordering
   *is* the survival policy.

3. **Per-document render cap.** Any single document over
   `PATIENT_DOCUMENT_MAX_RENDER_CHARS` (~320k chars ≈ 80k tokens) is
   reduced before the budget walk: it renders its stored AI summary
   (`extraction_metadata["summary"]`) if present, otherwise a head-clip
   with an explicit `[document truncated …]` marker. This keeps one giant
   PDF from consuming the whole budget or being dropped wholesale.

4. **Enforce the token budget.** `_enforce_budget` walks sources in
   **reverse priority order** (least important first). Truncatable
   sources shed rows from the tail until they fit or empty out;
   non-truncatable sources are dropped whole. Token counts use a
   deterministic char-based heuristic (`CHARS_PER_TOKEN`, ~4 chars/token
   for clinical English) — no tokenizer dependency. Default budget is
   `DEFAULT_TOKEN_BUDGET` (600k), well under the model window to leave
   room for the system prompt, history, and the response.

5. **Emit.** `_build_text` renders surviving sections in priority order;
   `_build_manifest` records ids, counts, and dates only — no PHI content.

The full priority table is §7.3 of the spec; don't re-enumerate it here.

## Invariants (the load-bearing guarantees)

These are what the eval tests in
`backend/tests/test_chat_context_bundler_doc_eval.py` pin down. Treat them
as contracts — if you change the bundler, keep them true.

- **Manifest always present.** The document *index*
  (`SOURCE_KEY_DOCUMENT_MANIFEST`) is non-truncatable and high enough
  priority that it survives even when full document bodies are
  budget-dropped. The model must always know a document *exists*, even if
  it couldn't read it this turn.
- **Safety plan survives.** `safety_plan_active` outranks document
  pressure and is kept under any realistic budget. Clinical-safety
  material is never the thing we drop.
- **Summary over truncation.** An over-cap document with a stored summary
  renders the summary (marked `[SUMMARY — full document loaded in brief]`)
  rather than a head-clipped body, so the reduction is visible to both the
  model and the therapist.
- **Pasted text is never silently truncated.** It has top priority; if it
  alone overflows the budget, assembly raises `ContextOverflowError`
  rather than quietly dropping the user's own input.
- **Manifest carries no PHI.** Ids, counts, and timestamps only.

## Relevance scoring

`_score_doc_relevance` is a deliberately cheap, dependency-free proxy: the
**overlap coefficient** between a document's word set and the query's word
set — `|doc ∩ query| / min(|doc|, |query|)`, lowercased whitespace tokens.

It uses the overlap coefficient rather than Jaccard on purpose. Jaccard
divides by the *union*, which grows with document length, so a long,
highly-relevant note scores *lower* than a short, barely-relevant one —
backwards for the goal of keeping the most relevant doc under pressure.
Dividing by the smaller set (in practice the query) removes that length
bias. `test_chat_context_bundler_doc_eval.py::TestRelevanceLengthBias`
demonstrates the flip directly.

This is a proxy, and a crude one. It has no term weighting, no stopword
removal, and no stemming. That is acceptable today **because the
architecture degrades gracefully**: a mis-ranked document still leaves its
manifest entry and (if over cap) its summary, so the cost of imperfect
ranking is bounded, not catastrophic. We invest in better ranking when
there's evidence retrieval quality is actually hurting — not before.

## Status vs the spec (as of 2026-06)

The spec ([`patient-context-chat-oss.md`](./patient-context-chat-oss.md)) is
canonical and current — the doc-context-quality work and the rendering-strategy
seam are reconciled into it. State of the engine:

**Shipped:**

- `document_manifest` source (§7.3, §7.9) — always-present, non-truncatable
  index of every uploaded doc.
- Relevance ordering of `patient_documents` (overlap coefficient).
- Per-doc render cap + summary fallback (`PATIENT_DOCUMENT_MAX_RENDER_CHARS`).
- `ContextBundle.documents` — per-item breakdown for citation/provenance.
- Rendering-strategy seam (§7.8) — `register_document_strategy`, the
  `strategy` selection field, and the `raw_text` default.

**Conforms to the spec:** selection shape + validation (§7.2), priority order
and pasted-text `ContextOverflowError` (§7.3), `default_source_selection`
(§7.4), the PHI-free manifest (§7.5), and the budget constants (§7.6).

**Not built (engine phases):** §7.7's `tools` field (a tool-driven strategy
needs a turn-service hook, not just the renderer seam — see §7.7), and the
spec-header phases 3b (LlmUsageMeter), 4 (frontend ChatPanel), 5 (retention
sweep + invariant checks), 6 (ops docs).

## Future changes

A living list. When you change context handling, add a dated line to "Recent"
and move roadmap items as they land. Keep roadmap framing honest about what
problem each item solves so we don't optimize ahead of need.

### Recent

- **2026-06 — rendering-strategy seam.** `register_document_strategy` + the
  `strategy` selection field; the engine ships `raw_text`. A deployment can
  register richer rendering strategies (summarized, structured, or
  retrieval-augmented) without modifying the engine.
- **2026-06 — doc-context-quality.** Document manifest always present;
  relevance ordering of uploaded docs; per-doc summary fallback over
  head-clip; relevance scorer switched from Jaccard to the overlap
  coefficient (fixes the length bias above).
- **2026-06 — windowed history.** `list_messages_windowed` (head K=2 + tail
  N=30) bounds prior-turn cost.

### Roadmap (deferred until there's a signal)

- **Better relevance ranking on the built-in proxy.** Stopword strip + term
  weighting, then length-normalized ranking. Cheap, keeps the no-dependency
  property; do it if `raw_text` doc ranking starts missing obvious matches.
- **Richer rendering strategies via the seam (§7.8).** The seam is the
  extension point — a deployment can plug in summarized, structured, or
  retrieval-augmented document rendering. Strategies that need their own
  tools or a multi-step fetch loop also need a turn-service hook (§7.7),
  which isn't built yet.
