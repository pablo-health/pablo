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
canonical but has drifted behind the code in a few places. Per its own
header rule ("code that diverges is a bug in the code *or* a needed
amendment to the doc"), these are amendments owed to the spec.

**Built beyond the spec (spec amendment owed):**

- **`document_manifest` source** — a 5th-priority, non-truncatable index of
  every uploaded doc (200-char previews). Not in §7.1's key list, §7.3's
  priority table, §7.4's default, or §7.5's reason strings. It's a
  selectable key (not auto-forced when `patient_documents` is on).
- **Relevance ordering** of `patient_documents` via `_score_doc_relevance`
  (overlap coefficient) — no mention in §7.
- **Per-doc render cap + summary fallback** —
  `PATIENT_DOCUMENT_MAX_RENDER_CHARS` (320k) clips a giant doc to its stored
  summary or a marked head-clip. Not in §7.
- **`ContextBundle.documents`** (`tuple[RetrievedDocument, …]`) — a per-item
  breakdown carrying chart material for citation/provenance.

**Deviation from the spec:**

- §7.7 specifies `ContextBundle.tools: list[ToolSpec]`. The code has **no
  `tools` field** — provenance arrived instead via the simpler `documents`
  breakdown above. The agentic tool surface (§7.8/§7.9) never landed, so the
  field it was designed around isn't there yet.

**Conforms to the spec:** selection shape + validation (§7.2), priority
order and pasted-text `ContextOverflowError` (§7.3), `default_source_selection`
(§7.4, exact match — `patient_documents`/`document_manifest` deliberately
out of the default), PHI-free manifest (§7.5), and the budget constants
(§7.6 — `DEFAULT_TOKEN_BUDGET=600k`, `CHARS_PER_TOKEN=4`,
`PASTED_TEXT_MAX_CHARS=32k`).

**Specced but not built:**

- **§7.8 strategy dispatch** — `summary_only` / `structured_fields`
  (ak6m.2.4). Today there is only the implicit `raw_text` behavior; the
  `strategy` selection field isn't parsed at all.
- **§7.9 agent fetch loop** (ak6m.2.5) — `ToolSpec`/`ToolResult`,
  `read_document_section` / `read_full_document` / `search_documents`, the
  three agent budgets, the `tool_calls` manifest array, and Gemini
  context-caching of the cheap preload. None built.
- Spec-header phases **3b** (LlmUsageMeter), **4** (frontend ChatPanel),
  **5** (retention sweep + invariant checks), **6** (ops docs).

## Future changes

A living list. When you change context handling, add a dated line to
"Recent" and move roadmap items as they land. Keep roadmap framing honest
about what problem each item solves so we don't optimize ahead of need.

### Recent

- **2026-06 — doc-context-quality.** Document manifest always present;
  relevance ordering of uploaded docs; per-doc summary fallback over
  head-clip; relevance scorer switched from Jaccard to the overlap
  coefficient (fixes the length bias above).
- **2026-06 — windowed history.** `list_messages_windowed` (head K=2 +
  tail N=30) bounds prior-turn cost.

### Roadmap (deferred until there's a signal)

Ordered cheapest-first. Each is intentionally **not** done yet:

- **Stopword strip + TF weighting on the relevance proxy.** Cheap, keeps
  the no-dependency property. Do this first if doc ranking starts missing
  obvious matches.
- **BM25-lite ranking.** Real term-frequency / length normalization.
  More code and tuning; only worth it once stopword+TF proves
  insufficient on real transcripts.
- **Agent fetch loop** (spec §7.8–§7.9). Instead of preloading and
  truncating, hand the model the manifest and let it fetch document bodies
  on demand. Changes the budget problem from "what to drop" to "what to
  request" and is the natural successor to relevance ranking.
- **Embedding / vector retrieval.** Semantic ranking over lexical. The
  heaviest option — new infrastructure and an embedding pipeline. Justify
  it against the agent-fetch approach before reaching for it; lexical
  ranking plus the manifest safety net covers a lot of ground first.
```
