# Patient-Context Chat — OSS Design Doc

**Status:** Living. Reflects HEAD as of 2026-06-02.
**Epic:** THERAPY-bhv (OSS patient-context chat primitive).
**Phases shipped:** 1 (lifecycle), 2 (context bundler), 3 (streaming turn service), doc-context-quality (document manifest, relevance ordering, per-doc render cap + summary fallback).
**Phases planned:** 3b (LlmUsageMeter), 4 (frontend ChatPanel + companion beads), 5 (retention sweep + invariant checks), 6 (operations docs).

> **New here?** Start with the runtime overview:
> [`chat-context-overview.md`](./chat-context-overview.md). This file is the
> detailed contract; that one is the map.

This file is the canonical specification the chat code already references
(`backend/app/services/chat_context_bundler.py:16`, `backend/app/models/chat.py:7`,
audit-action comments, route docstrings). Code that diverges from this doc
should be treated as a bug in the code or a needed amendment to this doc — not
"the code is right, the doc is stale."

The chat primitive is **OSS, AGPL, and clinically neutral by design.** Pablo
ships only the infrastructure: a chart-aware context bundler, a streaming LLM
gateway, lifecycle CRUD, audit hooks. The caller — an OSS UI surface or a SaaS
overlay — supplies the **system prompt**, **feature key**, and (optionally) the
**starter prompts** that frame *what kind of conversation this is* (session
prep, insurer continuation, peer note, patient homework summary, etc.). No
clinical opinion ships in OSS.

---

## §1. Goals and non-goals

### §1.1 Goals

- Let a clinician carry on a grounded, patient-specific conversation with the
  configured LLM. "Grounded" = every reply is anchored in a deterministic,
  PHI-aware bundle of chart context (notes, intake, treatment plan, etc.), not
  the model's free interpretation of an open-ended prompt.
- **Show its work.** A clinician can always see exactly which notes and
  documents went into the latest reply — surfaced via the per-message
  `context_manifest` (§7.5).
- Stay **caller-agnostic.** OSS ships no fixed feature workflow. The same
  primitive supports SaaS-overlay workflows (rx-justification, session prep,
  letter drafting), forensic review, and any future caller.
- **HIPAA-compliant by default.** All PHI flows through Vertex AI under
  Pablo's existing BAA; lifecycle events are audited; per-turn forensic
  detail lives on the `chat_messages` row, not in the audit log (§5).
- Honor token budgets cleanly. The context bundler walks sources in priority
  order, truncates row-level when it can, and produces a manifest that
  records every drop and its reason.
- Be feature-flagged. `settings.enable_patient_chat` defaults `False`. When
  off, every `/api/chat/*` URL falls through to the global 404 handler.

### §1.2 Non-goals

- **No clinical decision-making.** The model never recommends a diagnosis,
  prescription, or course of treatment by virtue of being Pablo. If a caller's
  system prompt frames the conversation that way, that's the caller's
  responsibility — OSS stays neutral.
- **No supervisor / chart-shared access.** A conversation is owned by the user
  who created it (§4). Multi-user access is a SaaS concern.
- **No tool-use / function-calling.** The model writes text. It doesn't issue
  search calls, run code, or trigger workflows. (Phase 6+ may revisit.)
- **No streaming SSE on the frontend until Phase 4.** Phases 1–3 expose the
  HTTP surface; the React component lands in Phase 4 (THERAPY-q3z).
- **No quota enforcement on by default.** The Phase-3b `LlmUsageMeter` (§11)
  records usage for forensic + future-tier purposes; OSS does not block on it.

---

## §2. Tenancy and ACL

Pablo is **schema-per-practice**: every per-practice table lives in a
`practice_<id>` schema, isolated from every other practice's data at the
Postgres level. Chat tables (`chat_conversations`, `chat_messages`) follow the
same convention — they have **no `tenant_id` column** because the schema
already disambiguates rows.

A user can act on a chat conversation if and only if:

1. They own it — `chat_conversations.owner_user_id == current_user.id`.
2. They have chart access to its patient — `PatientRepository.get(patient_id,
   user_id)` returns a non-null row.

Both gates are enforced in `backend/app/routes/chat.py::_authorize_conversation`.
The route layer returns **404, not 403**, for any failure of (1) or (2) so the
surface does not leak conversation existence to unauthorized callers (matching
the existing `/api/patients/*` behavior).

---

## §3. Data model

Two new tables in the practice schema. Both ship in Alembic revision
`c4e9a7b3f180_chat_conversations_and_messages`.

### §3.1 `chat_conversations`

| Column                       | Type                          | Notes                                                                 |
|------------------------------|-------------------------------|-----------------------------------------------------------------------|
| `id`                         | `VARCHAR(128) PRIMARY KEY`    | UUID4 string.                                                         |
| `patient_id`                 | `VARCHAR(128) NOT NULL`       | Immutable after insert (service-enforced).                            |
| `owner_user_id`              | `VARCHAR(128) NOT NULL`       | Immutable.                                                            |
| `title`                      | `VARCHAR(200) NOT NULL`       | Mutable. Seeded from patient name if omitted on create.               |
| `caller_system_prompt`       | `TEXT NOT NULL`               | Immutable after insert (service-enforced).                            |
| `caller_feature_key`         | `VARCHAR(64) NOT NULL`        | Immutable. Free-form caller-supplied tag (e.g. `session_prep`).       |
| `default_source_selection`   | `JSONB NULL`                  | Mutable.                                                              |
| `created_at`                 | `TIMESTAMP WITH TIME ZONE`    | Set at insert.                                                        |
| `last_turn_at`               | `TIMESTAMP WITH TIME ZONE`    | Bumped by the turn service after each assistant row finalizes.        |
| `archived_at`                | `TIMESTAMP WITH TIME ZONE`    | Soft-delete tombstone. `NULL` for active conversations.               |

**Check constraints:**

- `ck_chat_conversations_system_prompt_len`: `char_length(caller_system_prompt) BETWEEN 1 AND 16384`.
- `ck_chat_conversations_title_len`: `char_length(title) BETWEEN 1 AND 200`.

**Indexes:**

- `ix_chat_conversations_patient_id` on `patient_id`.
- `ix_chat_conversations_owner_user_id` on `owner_user_id`.
- `ix_chat_conversations_caller_feature_key` on `caller_feature_key`.
- `ix_chat_conversations_patient_last_turn` on `(patient_id, last_turn_at)`.
- `ix_chat_conversations_owner_last_turn` on `(owner_user_id, last_turn_at)`.

### §3.2 `chat_messages`

| Column               | Type                                    | Notes                                                            |
|----------------------|-----------------------------------------|------------------------------------------------------------------|
| `id`                 | `VARCHAR(128) PRIMARY KEY`              | UUID4.                                                           |
| `conversation_id`    | `VARCHAR(128) NOT NULL`                 | FK → `chat_conversations.id` `ON DELETE CASCADE`.                |
| `sequence`           | `INTEGER NOT NULL`                      | Monotonic per conversation starting at 1. See §14.               |
| `role`               | `VARCHAR(16) NOT NULL`                  | `user` or `assistant`.                                           |
| `content`            | `TEXT NOT NULL`                         | The turn's text.                                                 |
| `source_selection`   | `JSONB NULL`                            | The selection passed for this turn (user rows only).             |
| `context_manifest`   | `JSONB NULL`                            | PHI-free manifest captured at assembly time (user rows only).    |
| `input_tokens`       | `INTEGER NULL`                          | Estimated input tokens for the assembled prompt.                 |
| `output_tokens`      | `INTEGER NULL`                          | Counted by the gateway on completion (assistant rows).           |
| `llm_model`          | `VARCHAR(128) NULL`                     | Resolved at turn time; recorded on the assistant row.            |
| `llm_finish_reason`  | `VARCHAR(32) NULL`                      | One of `stop` \| `length` \| `safety` \| `error`.                |
| `llm_error`          | `VARCHAR(128) NULL`                     | Error code if the turn failed (`safety_block`, `timeout`, etc.). |
| `created_at`         | `TIMESTAMP WITH TIME ZONE NOT NULL`     | Set at insert.                                                   |

**Check constraints:**

- `ck_chat_messages_role`: `role IN ('user', 'assistant')`.
- `ck_chat_messages_content_len`: `char_length(content) BETWEEN 1 AND 32768`.

**Indexes:**

- `ix_chat_messages_conversation_id` on `conversation_id`.
- `ux_chat_messages_conversation_sequence` **unique** on `(conversation_id, sequence)`.

**Append-only.** The user row for a turn is inserted before assembly. The
assistant row is inserted in placeholder form at the same time the user row
is inserted, and **updated in place** as the stream completes — its `content`,
`output_tokens`, `llm_model`, `llm_finish_reason`, and `llm_error` are filled
in once the stream ends. No row deletion outside the conversation-delete
cascade.

---

## §4. Pydantic API models

All defined in `backend/app/models/chat_api.py`.

### §4.1 Requests

```python
class CreateChatConversationRequest(BaseModel):
    patient_id: str
    caller_feature_key: str = Field(min_length=1, max_length=64)
    caller_system_prompt: str = Field(min_length=1, max_length=16_384)
    title: str | None = Field(default=None, max_length=200)
    default_source_selection: dict[str, Any] | None = None


class UpdateChatConversationRequest(BaseModel):
    # Immutable fields (patient_id, caller_system_prompt, caller_feature_key,
    # owner_user_id) are intentionally NOT accepted here. To change the system
    # prompt, create a new conversation.
    title: str | None = Field(default=None, max_length=200)
    default_source_selection: dict[str, Any] | None = None
    archive: bool | None = None


class SendChatMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=32_768)
    source_selection: dict[str, Any] | None = None
    model: str | None = Field(default=None, max_length=128)
```

### §4.2 Responses

`ChatConversationResponse` is the list-view envelope (no messages).
`ChatConversationDetailResponse` extends it with `messages: list[ChatMessageResponse]`.
`ChatMessageResponse` carries every column on `chat_messages` except `content`
truncation rules (no truncation; full text).

---

## §5. Audit policy (two-tier)

Pablo's audit log (`AuditService`, `HIPAA_AUDIT_LOGS.md`) is the HIPAA
§ 164.312(b) record. It is intentionally **PHI-free**: it captures *who did
what, against what kind of resource, from where, when* — not the content of
that action. Per-turn forensic detail (the user's question text, the
assistant's reply, the manifest, the source selection) lives on
`chat_messages` rows where it can be hard-deleted by purge (§12).

### §5.1 Audited events

Defined in `backend/app/models/audit.py::AuditAction`:

| Action                        | Fired by                                                        | Triggering condition                                                                 |
|-------------------------------|-----------------------------------------------------------------|--------------------------------------------------------------------------------------|
| `CHAT_CONVERSATION_CREATED`   | `POST /api/chat/conversations`                                  | A new conversation row lands.                                                        |
| `CHAT_CONVERSATION_ARCHIVED`  | `PATCH /api/chat/conversations/{id}` or `DELETE …?mode=archive` | The boolean transition `archived_at: NULL → NOT NULL`.                               |
| `CHAT_CONVERSATION_PURGED`    | `DELETE /api/chat/conversations/{id}` (mode=purge, default)     | The conversation row and its messages have been hard-deleted.                        |
| `CHAT_TURN_BLOCKED`           | `POST .../messages` (during stream)                             | The turn ended with a `safety_block`, `context_too_large`, or `quota_exceeded` error.|
| `CHAT_CHART_PROMOTION`        | (reserved)                                                      | A future SaaS surface promotes a chat exchange into the chart as a note.             |

`CHAT_TURN_BLOCKED` is the only per-turn audit event, and it fires only on
**safety / quota / budget** failures. Successful turns are forensically
captured on the `chat_messages` row instead. This keeps the audit table small
and the lifecycle picture clean — auditors see "conversation existed, was
archived, was purged"; they do not see every back-and-forth.

### §5.2 What audit rows carry

Every chat audit row sets:

- `action`: one of the enum values above.
- `resource_type`: `CHAT_CONVERSATION`.
- `resource_id`: the conversation id.
- `actor_user_id`, `request_ip`, `user_agent`, `at`: as always.
- `changes` (JSONB): a short PHI-free diff:
  - On `CHAT_CONVERSATION_CREATED`: `{caller_feature_key, system_prompt_chars}`.
  - On `CHAT_CONVERSATION_ARCHIVED`: `{changed_fields: [...]}`.
  - On `CHAT_CONVERSATION_PURGED`: `{message_count}`.
  - On `CHAT_TURN_BLOCKED`: `{block_reason}`.

No turn content, no manifest, no note ids in `changes`. Anything that
identifies a specific note lives on `chat_messages.context_manifest`.

---

## §6. API surface

All endpoints mount under `/api/chat`. The router is conditionally mounted
based on `settings.enable_patient_chat`; when the flag is off, every URL
returns the global 404 handler. All endpoints require an authenticated user
who has accepted the BAA (`require_baa_acceptance`).

### §6.1 `POST /api/chat/conversations` — create

**Body:** `CreateChatConversationRequest`.
**Response:** `ChatConversationResponse`, status `201`.

**Behavior:** Verifies the user has chart access to `patient_id`; if not,
returns `404` (not `403`). Persists the conversation with the supplied
`caller_system_prompt` and `caller_feature_key` (both immutable from this
point on). Fires `CHAT_CONVERSATION_CREATED`. Seeds `title` from
`patient_display_name` if the caller omitted it.

### §6.2 `GET /api/chat/conversations/{conversation_id}` — detail

**Response:** `ChatConversationDetailResponse`. Messages returned in
`sequence` order. Errors: `404` if not authorized.

### §6.3 `GET /api/chat/conversations` — list

**Required query params:** `patient_id`.
**Optional:** `caller_feature_key`, `include_archived` (default `false`),
`page` (default `1`, `ge=1`), `page_size` (default `50`, `ge=1, le=100`).

**Response:** `ChatConversationListResponse` (`{ data: [...], total: <int> }`).
Returns `404` if the user lacks chart access to the patient. Archived
conversations are excluded unless `include_archived=true`.

### §6.4 `POST /api/chat/conversations/{conversation_id}/messages` — stream

**Body:** `SendChatMessageRequest`.
**Response:** `StreamingResponse`, media type `text/event-stream`, headers
`Cache-Control: no-cache`, `X-Accel-Buffering: no`.

**Pre-stream errors:**

- `404` — conversation not found / not owned by user / patient ACL fails.
- `409 Conflict` — conversation is archived (`archived_at IS NOT NULL`).
- `409 Conflict` — another turn is already in flight for this conversation
  (`TurnConcurrencyError`).
- `422 Unprocessable Entity` — content empty or > 32,768 chars.

**SSE event protocol** is documented in §8.

### §6.5 `PATCH /api/chat/conversations/{conversation_id}` — update

**Body:** `UpdateChatConversationRequest` (`title`, `default_source_selection`,
`archive`).

**Behavior:** Applies only the present fields. `archive=True` while already
archived is a no-op. `archive=False` clears `archived_at`. The
`CHAT_CONVERSATION_ARCHIVED` audit row fires only on the boolean transition
`false → true`.

### §6.6 `DELETE /api/chat/conversations/{conversation_id}` — delete

**Query param:** `mode` ∈ `{purge, archive}`. **Defaults to `purge`** —
that is the irreversible content-deletion path. `mode=archive` is a soft-delete
synonym for `PATCH … {archive: true}`.

**`mode=purge`** drops the conversation row and cascades to its messages.
Fires `CHAT_CONVERSATION_PURGED` with `{message_count}`. Once purged, the
data is gone — the audit row remains as the only durable record that the
conversation ever existed.

---

## §7. Context bundler

Defined in `backend/app/services/chat_context_bundler.py`. Pure function over a
patient's chart: given a typed `SourceSelection`, the assembler walks each
selected source, renders it to bounded text, estimates tokens, enforces a
budget, and produces a `ContextBundle` (text + PHI-free manifest +
total tokens).

### §7.1 Source keys (V1)

```python
SOURCE_KEY_PASTED_TEXT             = "pasted_text"
SOURCE_KEY_CURRENT_MEDICATIONS     = "current_medications"
SOURCE_KEY_MOST_RECENT_INTAKE      = "most_recent_intake"
SOURCE_KEY_PROGRESS_NOTES_RECENT   = "progress_notes_recent"
SOURCE_KEY_PROGRESS_NOTES_EXPLICIT = "progress_notes_explicit"
SOURCE_KEY_DOCUMENT_MANIFEST       = "document_manifest"     # doc-context-quality: always-present index
SOURCE_KEY_PATIENT_DOCUMENTS       = "patient_documents"
SOURCE_KEY_TREATMENT_PLAN_ACTIVE   = "treatment_plan_active"
SOURCE_KEY_SAFETY_PLAN_ACTIVE      = "safety_plan_active"
SOURCE_KEY_LAB_VALUES_RECENT       = "lab_values_recent"   # stub — module_not_available
SOURCE_KEY_VITALS_RECENT           = "vitals_recent"        # stub — module_not_available
```

The frozen tuple `V1_SOURCE_KEYS` is the closed set the assembler accepts. SaaS
overlays import it for tier gating.

### §7.2 Source selection shape

A selection is a JSON-serializable dict keyed by source key. Each value is
either:

- `True` — include the source with its default params.
- A dict with source-specific params (e.g.
  `{"limit": 5, "include_transcripts": false}` for `progress_notes_recent`;
  `{"content": "free-text snippet"}` for `pasted_text`;
  `{"note_ids": [...]}` for `progress_notes_explicit`;
  `{"limit": 5}` or `{"document_ids": ["uuid", ...]}` for `patient_documents`
  — `limit` and `document_ids` are mutually exclusive; `limit` is capped at
  `PATIENT_DOCUMENTS_LIMIT_MAX = 50`).
- `False` / missing — skip.

The bundler raises `InvalidSelectionError` on:

- Unknown keys (not in `V1_SOURCE_KEYS`).
- Wrong-shape values for a known key (e.g. `progress_notes_explicit` with a
  non-string id, `patient_documents` with both `limit` and `document_ids`).
- `patient_documents` selected without a `patient_documents_repo` passed to
  `assemble_context_bundle` — the bundler does not import the Postgres impl at
  module load and refuses to assemble a source whose backing repo wasn't
  supplied.

### §7.3 Priority order (truncation order)

When the assembled context exceeds the budget, sources are walked in
**reverse priority order** (lowest priority dropped first). Truncatable
sources have their rows dropped one at a time before the source itself is
dropped.

| Priority | Source                              | Truncatable? |
|----------|-------------------------------------|--------------|
| 1        | `pasted_text`                       | No           |
| 2        | `current_medications`               | No           |
| 3        | `safety_plan_active`                | No           |
| 4        | `most_recent_intake`                | No           |
| 5        | `progress_notes_explicit`           | Yes (row-level) |
| 5        | `document_manifest`                 | No           |
| 6        | `patient_documents`                 | Yes (row-level) |
| 7        | `progress_notes_recent`             | Yes (row-level) |
| 8        | `treatment_plan_active`             | No           |
| 9        | `lab_values_recent`, `vitals_recent`| No (stub)    |

`document_manifest` (doc-context-quality) shares priority 5 with
`progress_notes_explicit` but is **non-truncatable**: it's a compact index
(filename + 200-char preview per uploaded doc), so it's kept whole or not
at all. Sitting at priority 5 — above `patient_documents` (6) — means the
*index* of every document survives even when the full document bodies are
budget-dropped, so the model always knows a document exists. It is a
separate selectable key, not auto-added when `patient_documents` is on.

`patient_documents` (THERAPY-ak6m.2.2) sits between explicit progress notes
and the recent-progress-notes window. The reasoning: uploaded chart artifacts
(prior-provider PDFs, intake packets, lab printouts) are clinician-curated
chart material — closer in stature to a progress note than to a stub source —
but a generic upload set carries less explicit intent than
`progress_notes_explicit`, which the clinician picked by id. Placing it
above `progress_notes_recent` means a PMHNP whose chart history lives in
PDFs keeps that history under budget pressure before the most-recent N
SOAP notes are trimmed.

**Pasted-text overflow** is a special case: if pasted text alone exceeds the
budget, the bundler raises `ContextOverflowError` *before* assembly proceeds.
That maps to a `context_too_large` SSE error code at the route. Pasted text is
intentionally never truncated — the clinician chose the exact snippet.

### §7.4 Default selection

```python
def default_source_selection() -> dict[str, Any]:
    return {
        SOURCE_KEY_CURRENT_MEDICATIONS: True,
        SOURCE_KEY_MOST_RECENT_INTAKE: True,
        SOURCE_KEY_PROGRESS_NOTES_RECENT: {"limit": 3, "include_transcripts": False},
        SOURCE_KEY_TREATMENT_PLAN_ACTIVE: True,
        SOURCE_KEY_SAFETY_PLAN_ACTIVE: True,
        SOURCE_KEY_LAB_VALUES_RECENT: {"limit": 5},
        SOURCE_KEY_VITALS_RECENT: {"limit": 5},
    }
```

Callers pass their own `defaultSourceSelection` on conversation create; this
function is the OSS recommended baseline.

`patient_documents` is intentionally **not** in the chat default. Turning it on
globally would change context shape for every existing chat conversation
without an explicit opt-in, including conversations whose patients have a
large legacy chart attached as PDFs (budget pressure, surprise PHI surface).
The pre-visit-brief (THERAPY-ak6m.1) and letter-generator (THERAPY-ak6m.3)
callers should opt in via their own `defaultSourceSelection` when those
beads ship — both rely on uploaded chart history as primary input, where
the chat surface treats it as opt-in supplemental context.

### §7.5 Manifest shape

The manifest is **PHI-free**: it references notes by id and counts rows; it
never contains note content, patient names, or clinical text.

```jsonc
{
  "sources_included": [
    {
      "source_key": "progress_notes_recent",
      "tokens_est": 1250,
      "row_count": 3,
      "note_ids": ["uuid1", "uuid2", "uuid3"],
      "rows_dropped": 1,
      "dropped_note_ids": ["uuid4"]
    },
    {
      "source_key": "current_medications",
      "tokens_est": 120,
      "row_count": 1,
      "note_ids": ["uuid5"]
    },
    {
      "source_key": "pasted_text",
      "tokens_est": 800,
      "chars": 3200
    },
    {
      "source_key": "patient_documents",
      "tokens_est": 2400,
      "row_count": 2,
      "document_ids": ["uuid-doc-1", "uuid-doc-2"],
      "skipped_no_text": 1
    }
  ],
  "sources_dropped": [
    {"source_key": "lab_values_recent", "reason": "module_not_available"},
    {"source_key": "vitals_recent",      "reason": "module_not_available"}
  ],
  "total_tokens_est": 4800,
  "token_budget": 600000,
  "patient_id": "<uuid>",
  "assembled_at": "2026-05-13T21:00:00Z"
}
```

Reason strings the manifest can emit:

- `module_not_available` — source loader is a stub (currently `lab_values_recent`, `vitals_recent`).
- `budget` — source/row dropped to fit under the token budget.
- `no_data` — selected but the patient has no rows of this type.
- `invalid_selection` — caller-supplied params were malformed.

### §7.6 Token budget

`DEFAULT_TOKEN_BUDGET = 600_000`. Estimated with a deterministic char-based
heuristic — `CHARS_PER_TOKEN = 4` — chosen because it's stable across machines
and doesn't require a live tokenizer. The actual Gemini token count may vary
slightly; the budget includes generous headroom for that.

Per-source cap: `PASTED_TEXT_MAX_CHARS = 32_000` (the same as
`SendChatMessageRequest.content` max).

### §7.7 ContextBundle output

```python
@dataclass(frozen=True)
class ContextBundle:
    text: str                                  # ready-to-splice context block
    manifest: dict[str, Any]                   # per §7.5; persisted on the user-turn row
    total_tokens_est: int                      # estimated tokens consumed by text
    documents: tuple[RetrievedDocument, ...]   # per-item breakdown (doc-context-quality)

@dataclass(frozen=True)
class RetrievedDocument:
    source_key: str       # which selection source produced this item
    document_id: str      # note id / patient-document id (or source key for synth blocks)
    text: str             # the item's rendered content — chart material, treat as PHI
    tokens_est: int
```

`documents` is the structured, per-item counterpart to the flattened `text`
blob, reflecting the *final* (post-truncation) set the model received. It
carries chart material, so it is **never** persisted on the (PHI-free)
manifest — it exists so retrieval quality can be evaluated per document
(relevance to the question) rather than only in aggregate.

> **Note:** earlier drafts of this section specced a `tools` field for a
> tool-driven rendering strategy. That field was never built — per-document
> provenance arrived instead via `documents` above. A strategy that needs
> its own tools or a multi-step fetch loop would require an additional
> turn-service hook (it cannot ride the renderer seam in §7.8 alone), and
> would add `tools` alongside `documents`, not in place of it.

### §7.8 Document rendering strategies (extension seam)

How a `patient_documents` source is rendered into prompt text is pluggable.
The per-source selection dict may carry a reserved `strategy` field; the
bundler dispatches on it:

```python
{SOURCE_KEY_PATIENT_DOCUMENTS: True}                            # default strategy
{SOURCE_KEY_PATIENT_DOCUMENTS: {"strategy": "raw_text"}}        # explicit
{SOURCE_KEY_PATIENT_DOCUMENTS: {"strategy": "raw_text", "limit": 5}}
```

A strategy is a renderer over the **final** (access-checked,
relevance-ordered, possibly budget-truncated) document set:

```python
DocumentRenderer = Callable[[list[PatientDocument]], str]

def register_document_strategy(
    name: str, renderer: DocumentRenderer, *, replace: bool = False
) -> None: ...
```

- The engine ships exactly one strategy, **`raw_text`** — the full extracted
  text of each doc, with the per-doc render cap and summary fallback from
  §7.9. It is the default when `strategy` is omitted, so existing
  conversations never shift behavior.
- A deployment can register additional renderers at import time (e.g.
  summary-only or structured-field rendering) and select them per source via
  `strategy`. Unknown / unregistered strategy values are rejected with
  `InvalidSelectionError`, the same as an unknown source key.
- The renderer signature is fixed on purpose: the truncation, manifest, and
  budget code never learn which strategy ran. When the budget walk drops a
  row it re-renders through the *same* strategy (looked up by the `strategy`
  name recorded on the source's manifest entry), so a custom strategy stays
  consistent under truncation.
- The manifest records the strategy name on the `patient_documents` entry (a
  string — PHI-free).

This is a renderer seam, not a general plugin framework. A strategy that
needs to register tools or run a multi-step fetch loop within a turn needs an
additional turn-service hook (see §7.7's note); the renderer registry alone
only governs how the already-selected documents become text.

**Where a per-caller policy check belongs:** the bundler trusts the selection
shape. If a feature must use a specific strategy, enforce that at the route
layer — read `caller_feature_key`, validate the strategy against an
allow-list, 422 on violation. Not the bundler's job.

### §7.9 Document context handling (shipped — doc-context-quality, 2026-06)

The `raw_text` document path (§7.8) gained three behaviors that close the
gap between "load whole documents" and "fit the budget" without loading
every document body on every turn. All three are in
`chat_context_bundler.py` and pinned by
`backend/tests/test_chat_context_bundler_doc_eval.py`.

**Document manifest.** `SOURCE_KEY_DOCUMENT_MANIFEST` (priority 5,
non-truncatable; see §7.3) renders a compact index — filename + a
`DOCUMENT_MANIFEST_PREVIEW_CHARS = 200` preview (the stored summary if
present, else the head of the extracted text) — for every uploaded doc.
Because it outranks `patient_documents` and is never truncated, the model
always learns a document *exists* even when budget pressure drops the full
bodies. Invariant: the manifest is present whenever selected, deduplicated,
and empty-of-entries (not absent) when the patient has no docs.

**Relevance ordering.** When the turn carries a `query`,
`_load_patient_documents` sorts usable docs most-relevant-first by
`_score_doc_relevance`. The budget walk drops rows from the *tail*
(§7.3), so ordering is the survival policy. The score is the **overlap
coefficient** — `|doc ∩ query| / min(|doc|, |query|)` over lowercased
whitespace tokens — chosen over Jaccard because Jaccard's union denominator
grows with document length and would rank a long, on-topic note *below* a
short, barely-relevant one. With no query, order falls back to
newest-first (`sorted` is stable, all scores 0.0). It is a deliberately
cheap, dependency-free proxy: no term weighting, stopword removal, or
stemming (those are roadmap — see the strategy seam §7.8 and the overview's "Future changes").

**Per-doc render cap + summary fallback.** A single document over
`PATIENT_DOCUMENT_MAX_RENDER_CHARS` (320k chars ≈ 80k tokens) is reduced
*before* the budget walk so one giant PDF can't consume the whole budget or
be dropped wholesale. If `extraction_metadata["summary"]` exists it renders
the summary marked `[SUMMARY — full document loaded in brief]`; otherwise it
head-clips with an explicit `[document truncated — N chars omitted]` marker.
Either way the reduction is visible to the model and the therapist.

**Manifest forensics.** The `patient_documents` entry gains
`document_ids` (survivors), `dropped_document_ids` + `rows_dropped` (tail
docs shed for budget), and `skipped_no_text` (uploads with no extracted
text). Still PHI-free — ids and counts only.

See [`chat-context-overview.md`](./chat-context-overview.md) for the
runtime map and the deferred-work rationale.

---

## §8. Turn service, gateway, retry, errors

`ChatTurnService` (`backend/app/services/chat_turn_service.py`) orchestrates a
single turn end-to-end. It:

1. Acquires a per-conversation lock (raises `TurnConcurrencyError` if another
   turn is in flight).
2. Allocates `sequence` numbers, inserts the user row and an assistant
   placeholder row.
3. Calls `assemble_context()` with the resolved `SourceSelection` (overriding
   `default_source_selection` if the request supplied one).
4. Builds the prompt envelope and streams from the gateway.
5. On `delta` from the gateway, emits an SSE `delta` event with the same text.
6. On gateway `finish_reason`, finalizes the assistant row (`content`,
   `output_tokens`, `llm_model`, `llm_finish_reason`) and emits an SSE `done`.
7. On gateway error, classifies the error code, finalizes the assistant row
   with `llm_error`, emits an SSE `error`. If the error code is retryable,
   first attempts a retry per §8.3.

### §8.1 SSE event protocol (wire shape)

The route layer is a thin JSON encoder over `TurnStreamEvent`. The shape:

```
event: meta
data: {"user_message_id":"...","assistant_message_id":"...","input_tokens":4500,"model":"gemini-2.5-flash-lite","manifest":{...}}

event: delta
data: {"text":"<chunk>"}

event: delta
data: {"text":"<chunk>"}

event: done
data: {"output_tokens":820,"finish_reason":"stop"}
```

On failure, the stream ends with `error` instead of `done`:

```
event: error
data: {"error":"<error_code>","message":"<human_readable>"}
```

`meta` is emitted exactly once at the start. `delta` is emitted zero or more
times. `done` is emitted exactly once on success. `error` is emitted exactly
once on failure (and is mutually exclusive with `done`).

### §8.2 Error vocabulary

Every value that can appear in `error.error`:

| Code                  | Meaning                                                                                 | Retryable? |
|-----------------------|-----------------------------------------------------------------------------------------|------------|
| `empty_message`       | User message empty/whitespace-only.                                                     | No         |
| `message_too_long`    | User message exceeds 32,768 chars. (Also a 422 pre-stream from FastAPI validation.)     | No         |
| `context_too_large`   | Pasted-text source alone exceeds the token budget; no assembly possible.                | No         |
| `invalid_selection`   | Source selection structurally invalid (unknown key, wrong shape).                       | No         |
| `safety_block`        | Gemini safety filter blocked the response. `llm_finish_reason="safety"`.                | No         |
| `quota_exceeded`      | LlmUsageMeter denied the turn (Phase 3b; off by default in OSS).                        | No         |
| `timeout`             | Gateway timeout / deadline exceeded.                                                    | Yes        |
| `service_unavailable` | Vertex / Gemini returned 503 / "unavailable".                                            | Yes        |
| `auth_denied`         | Gateway returned 401/403. Bug or misconfig; should not happen at runtime.               | No         |
| `llm_error`           | Catch-all for unexpected gateway exceptions.                                            | Yes        |
| `concurrent_turn`     | Another turn already in flight; emitted by the route layer (not the gateway).           | No         |

### §8.3 Retry policy

`MAX_GATEWAY_ATTEMPTS = 2` (one initial attempt + one retry).
`RETRY_BACKOFF_SECONDS = 1.0`.

Retryable: `{"timeout", "service_unavailable", "llm_error"}`. Anything else
fails fast.

On retry: deltas emitted during the failed attempt are **suppressed** from the
SSE stream — the client sees only the final attempt's deltas. The retry is
opaque to the consumer; only `meta` and the eventual `done` / `error` reach
the client.

### §8.4 Prompt envelope

The gateway receives:

- `system_instruction`: the conversation's `caller_system_prompt`, prepended
  with an OSS-supplied wrapper that names the assistant as a chart-aware
  summarization tool and forbids clinical recommendations. (The wrapper is
  intentionally generic — caller prompts add the workflow-specific framing.)
- `prior_turns`: every prior `chat_messages` row for this conversation, in
  `sequence` order, mapped to Gemini `Content` blocks (`user` → `user`,
  `assistant` → `model`).
- `new_user_text`: the context block (`ContextBundle.text`) concatenated with
  the current user message.

`temperature = 0.4`. `max_output_tokens` per gateway default.

---

## §9. Authorization

Already summarized in §2. Implementation lives in
`backend/app/routes/chat.py::_authorize_conversation`. Two gates:

1. `chat_service.get_conversation(id).owner_user_id == user.id`.
2. `patient_repo.get(conv.patient_id, user.id) is not None`.

Both gates collapse to `404` on failure. Cross-user / cross-patient access
does not leak via timing or response shape.

Supervisor / shared access is deferred — see Phase-5+ open decisions.

---

## §10. Audit events (per-action detail)

Already enumerated in §5. Implementation lives in
`AuditService.log_chat_action(action, user, request, conversation_id, patient_id, changes)`.

The single per-turn audit event (`CHAT_TURN_BLOCKED`) is fired from the
streaming route's SSE generator — see `chat.py:441` — wrapped in
`try/except` so that an audit-write failure never breaks the stream.

---

## §11. LLM model selection + usage metering

### §11.1 Settings

```python
ai_model: str = "gemini-2.5-pro"
ai_model_flash: str = "gemini-2.5-flash-lite"
enable_patient_chat: bool = False
```

### §11.2 OSS resolver

```python
def default_resolve_chat_model(*, user, feature_key, override=None) -> str:
    if override:
        return override
    settings = get_settings()
    return settings.ai_model_flash or settings.ai_model
```

The OSS resolver is **deliberately tier-blind.** It honors a per-conversation
`override` from `SendChatMessageRequest.model`, then falls back to the flash
model (or `ai_model` if flash is unset).

### §11.3 Dependency-injection hook

`get_chat_model_resolver()` is a FastAPI dependency that returns the active
resolver. SaaS overlays substitute via
`app.dependency_overrides[get_chat_model_resolver] = saas_resolver`. The OSS
hook signature accepts `user` and `feature_key` so the substituted resolver
can implement tier-aware policy (Solo → flash-lite, Practice+ → Pro,
per-feature pin for rx-justification, etc.).

OSS itself **must not** ship any feature-keyed routing. AGPL safeguard.

### §11.4 Gateway implementation

`GeminiChatLLMGateway` lives in `backend/app/services/chat_llm_gateway.py`. It:

- Uses `google.genai` in **Vertex AI mode** (HIPAA-covered BAA endpoint). Not
  AI Studio.
- Runs the SDK's synchronous generator on a thread pool so the route's async
  loop is not blocked.
- Maps Gemini finish reasons to the OSS finish-reason vocabulary
  (`SAFETY` / `PROHIBITED_CONTENT` / `RECITATION` → `safety`; `MAX_TOKENS` → `length`;
  `STOP` → `stop`).
- Classifies exceptions into the §8.2 error vocabulary deterministically by
  type name and lower-cased message keywords (`timeout`, `deadline`,
  `unavailable`, `503`, `permission`, `401`, `403`).

### §11.5 LlmUsageMeter (Phase 3b — planned)

To land in Phase 3b (THERAPY-bhv follow-up bead). Public interface:

```python
class LlmUsageMeter:
    def record_turn(self, *, conversation_id, input_tokens, output_tokens, model, at): ...
    def get_period_usage(self, *, user_id, period_start, period_end) -> tuple[int, int]: ...
    def check_quota(self, *, user_id, required_tokens) -> bool: ...
```

Quota enforcement is **off by default** in OSS. `check_quota` returns `True`
unless `settings.chat_quota_enforced` is set. SaaS overlays may flip the flag
and configure per-tier quotas.

### §11.6 Per-turn `llm_model` recording

Every assistant `chat_messages` row records the resolved model in `llm_model`.
Forensic auditors can answer "which model produced this reply?" without
running the resolver against historical state.

---

## §12. Retention, archive, purge

Three lifecycle states:

- **Active.** `archived_at IS NULL`. Composer enabled; turns appendable.
- **Archived.** `archived_at IS NOT NULL`. Conversation row + messages still
  present. Composer disabled (`409` on `POST /messages`). Lists exclude
  archived unless `include_archived=true` query param.
- **Purged.** Conversation row and messages dropped. Only the audit row
  (`CHAT_CONVERSATION_PURGED`) remains.

### §12.1 Soft-delete (archive)

`PATCH … {archive: true}` or `DELETE … ?mode=archive`. Sets `archived_at`.
Reversible by `PATCH … {archive: false}`. The audit event fires only on the
`false → true` transition (no double-audit on idempotent archives).

### §12.2 Hard-delete (purge — default `DELETE` behavior)

`DELETE …` (no `mode` param, or `mode=purge`). FK cascade drops messages.
Fires `CHAT_CONVERSATION_PURGED` with `{message_count}`. Audit row is the only
remaining trace.

### §12.3 Retention sweep (Phase 5 — planned)

A scheduled job will mark long-stale active conversations for archive, and
long-stale archived conversations for purge. The exact policy
(`90 days inactive → archive; 180 days archived → purge`?) is a Phase-5
open decision.

---

## §13. Frontend component contract

The OSS frontend ships a single React component, `ChatPanel`, mountable by
any caller (OSS dashboard, SaaS overlay workflows, future surfaces).
`ChatPanel` is intentionally **callable, not routed**: there is no
`/app/chat` route in OSS. Callers embed the component in their own surface
(e.g. a patient-detail sidebar) and supply the per-conversation framing.

The visual language follows Pablo's existing tokens
(`--color-primary-*` / `--color-secondary-*` / `--color-neutral-*`,
DM Sans body + Fraunces display, no framer-motion). Implementation uses
Tailwind v4 + shadcn primitives (`Button`, `Dialog`, `Popover`,
`DropdownMenu`, `Textarea`).

### §13.1 Prop API (baseline — THERAPY-q3z)

```ts
interface ChatPanelProps {
  patientId: string;
  callerFeatureKey: string;            // e.g. "session_prep", "rx_justification"
  callerSystemPrompt: string;          // immutable for the conversation lifetime
  defaultSourceSelection?: SourceSelection;
  conversationId?: string;             // optional resume
  title?: string;                      // caller-supplied conversation title
  className?: string;
  onArchived?: (conversationId: string) => void;
}

type SourceKey =
  | "pasted_text"
  | "current_medications"
  | "most_recent_intake"
  | "progress_notes_recent"
  | "progress_notes_explicit"
  | "treatment_plan_active"
  | "safety_plan_active"
  | "lab_values_recent"
  | "vitals_recent";

type SourceSelection = Partial<Record<SourceKey, SourceParams>>;
```

The exact `SourceParams` shape per key mirrors §7.2.

### §13.2 Source-chip rail

A persistent horizontal pill rail sits above the message thread. One chip per
source key in the current selection.

- Chips are color-banded on the **left edge** by source family:
  - *Sessions* (sage) — `progress_notes_recent`, `progress_notes_explicit`.
  - *Documents* (honey) — `most_recent_intake`, `treatment_plan_active`,
    `safety_plan_active`, `current_medications`, `lab_values_recent`,
    `vitals_recent`.
  - *Manual* (neutral) — `pasted_text`.
- Each chip displays: lucide icon + short label + small secondary metadata
  (e.g. *"5 progress notes · last May 9"*).
- Active state = filled family tint. Inactive = outlined w/ muted text.
- A click on the chip body toggles inclusion for the next turn (mutates
  local `source_selection` state only — the conversation's
  `default_source_selection` is not changed).
- A click on a chip's metadata caret (or long-press) opens a **popover**
  carrying the per-source forensic detail: contributing note ids as links
  (open the existing session-view page in a new tab), token estimate, drop
  count, and a "Set as default for this conversation" button that `PATCH`es
  `default_source_selection` server-side.
- A trailing `+ Add source` chip opens a small menu of V1 source keys not
  currently in the selection. Keys reported as `module_not_available` in the
  latest manifest are filtered out.

The chip rail **renders before any reply arrives**, derived from
`defaultSourceSelection` plus a fast `/messages` precheck the panel performs
on first mount to surface `module_not_available` reasons up-front.

### §13.3 Per-message manifest disclosure

Under every assistant bubble, a single small caret line summarizes the
manifest captured at that turn:

> *Based on 5 progress notes, intake, medications · 4.8k tokens*

Caret-click expands an inline detail block: every `sources_included` entry
with row count, dropped count, and clickable note-id links (open session view
in a new tab). The expansion uses the message's stored `context_manifest`,
not the current chip-rail selection — so the disclosure stays accurate even
after the user later toggles chips.

### §13.4 Briefing card (empty state) — Bead 4c

On mount with no messages, the empty state is replaced by a **briefing card**
— a sage-tinted card with a Fraunces-italic line composed client-side from
the resolved `defaultSourceSelection` plus a freshly fetched manifest preview.

Composition rules:

- Lead with *"I'm reading {{patient.firstName}}'s …"* (lay-friendly verb,
  no clinical voice).
- Comma-separate the sources by document type (e.g. *"the most recent intake
  from March 3, the active treatment plan, the active safety plan, and 5
  most recent progress notes (last from May 9)"*).
- Omit any source whose manifest entry has `row_count: 0` from the sentence —
  do not say "no safety plan."
- End with a neutral invitation: *"Ask me anything."*
- No clinical opinion. No diagnostic terms. No interpretation of chart
  content.

If `starterPrompts` is populated (§13.5), a single row of starter chips
renders **below** the briefing line, inside the same card.

The card disappears once the first user message lands.

### §13.5 Caller-supplied starter prompts — Bead 4d

A second prop extends `ChatPanelProps`:

```ts
interface ChatPanelProps {
  // ... §13.1 props ...
  starterPrompts?: StarterPrompt[];
}

interface StarterPrompt {
  id: string;
  label: string;                       // e.g. "Brief letter (employer)"
  icon?: LucideIcon;
  prompt: string;                      // the editable text to pre-fill the composer
  sourceSelectionOverride?: SourceSelection;
}
```

**OSS must ship `starterPrompts === undefined` by default.** OSS never bakes
clinical-workflow opinions into the component. The SaaS overlay (or any other
caller) supplies its own template list per `callerFeatureKey` (work-excuse
letter, insurer continuation, peer/PCP note, patient homework summary in lay
terms, dr-to-dr clinical note, ESA letter, etc.).

**UI behavior.** When the prop is populated:

- Render the chips as a single row inside the briefing card, below the
  briefing line.
- On chip click: pre-fill the composer with `prompt` (editable, focus the
  textarea, place caret at end). If `sourceSelectionOverride` is set, replace
  the current chip-rail selection with the override **for the next turn
  only** — restore the prior selection after send.
- Chips disappear once the first user message lands (empty-state only). To
  re-scope mid-conversation, the user types freely
  ("now rewrite that as a one-paragraph employer letter — no clinical
  detail").

Mid-conversation reopener (a `+` button next to send) is **out of scope**
until usage data justifies it.

### §13.6 System-prompt view — Bead 4c

A small chevron `i` icon sits next to the conversation title in the panel
header. Click expands a read-only collapsible region:

> *Using the **session-prep** prompt:*
> > [verbatim `caller_system_prompt` text, monospace, scrollable for long
> > prompts]

Closed by default. No edit affordance — `caller_system_prompt` is immutable
per §3.1.

The affordance answers Frontiers 2025's *"how on earth does AI decide?"*
literally: this is exactly what it was told to do.

### §13.7 Scope / safety footer — Bead 4c

A single small line below the composer, `text-xs text-neutral-500`:

> *"Pablo Chat summarizes chart context. Not a clinical decision tool. PHI
> stays in this practice; conversations are purged on delete."*

Static text. No link. Persistent for the conversation lifetime. Matches the
APA chatbot health advisory's "clear, prominent disclaimer" requirement —
factual scope statement, not clinical voice.

### §13.8 Error states

Distinct copy per §8.2 error code. All non-clinical, all action-oriented:

| Error code            | Inline message                                                                                  | Remedy button                |
|-----------------------|-------------------------------------------------------------------------------------------------|------------------------------|
| `context_too_large`   | "The selected sources are too large for a single reply. Try unchecking older notes or removing pasted text." | "Reset to defaults" |
| `safety_block`        | "The model declined to respond. Try rephrasing or shortening the question."                     | —                            |
| `llm_error`           | "We couldn't reach the model."                                                                  | "Retry"                      |
| `timeout`             | "We couldn't reach the model."                                                                  | "Retry"                      |
| `service_unavailable` | "We couldn't reach the model."                                                                  | "Retry"                      |
| `concurrent_turn`     | "Another response is still streaming for this conversation."                                    | —                            |
| `quota_exceeded`      | "Chat quota for this period has been reached."                                                  | —                            |
| `invalid_selection`   | "One of the selected sources isn't available."                                                  | "Reset to defaults"          |
| `empty_message`       | (Composer-side validation; not surfaced as an inline error.)                                    | —                            |
| `message_too_long`    | (Composer-side validation; meter turns red.)                                                    | —                            |

### §13.9 Bubble visual spec

- **User bubble:** right-aligned. `bg-primary-100` (honey-50/100 tint).
  `text-neutral-900`. `rounded-2xl rounded-br-md`. `px-4 py-2.5`.
- **Assistant bubble:** left-aligned. `bg-white border border-neutral-200
  shadow-sm`. `rounded-2xl rounded-bl-md`. During streaming, a small
  honey-tinted three-dot indicator sits at the bubble's tail while `delta`
  events flow.
- **Date separators** between turns from different days are rendered as a
  small Fraunces-italic centered label.

### §13.10 Composer

- Auto-resize textarea, max ~8 lines. DM Sans 15px.
- `Enter` sends. `Shift+Enter` newlines.
- Honey-filled send button (lucide `Send`); disabled while a turn is
  streaming or the textarea is empty.
- Thin token-budget meter under the textarea, dormant until typed text crosses
  50% of the remaining budget. Fills sage → amber → red across 50% / 75% /
  95%. Preflight UX for the `context_too_large` failure.

### §13.11 Archive flow

An overflow menu (`MoreHorizontal` lucide icon) in the panel header lists
"Archive conversation." Confirm dialog → `PATCH { archive: true }` →
`onArchived?(conversationId)` callback → panel switches to a read-only
archived state with a footer line "This conversation is archived." Composer
is hidden.

### §13.12 SSE client

A small frontend helper, `streamChatMessages(url, body, { onMeta, onDelta,
onDone, onError, signal })`, wraps `fetch + getReader()` and a line-based SSE
parser. Auth header is shared with the existing `apiClient` Firebase-token
flow via a refactored `getAuthHeader()` helper.

Retry is **not** implemented client-side — the backend handles retryable
errors per §8.3 and only ever surfaces a final outcome to the client. The
"Retry" remedy button on `llm_error` / `timeout` / `service_unavailable` is
a user-triggered re-send, not an automatic background retry.

---

## §14. Concurrency

Per-conversation turn serialization is enforced at two layers:

1. `ChatTurnService` holds a `dict[conversation_id, asyncio.Lock]`. Acquisition
   is non-blocking; if the lock is held, the service raises
   `TurnConcurrencyError` immediately.
2. `ChatRepository.next_sequence(conversation_id)` issues a row-locking
   `SELECT ... FOR UPDATE` against the parent `chat_conversations` row,
   serializing sequence allocation across processes.

The route layer translates `TurnConcurrencyError` to a `409` pre-stream
response. If the lock is lost mid-stream (process death, replica failover),
the next attempt's `next_sequence` call will block briefly on the row lock
and proceed normally.

---

## §15. Settings

```python
# Patient-context chat primitive (THERAPY-bhv).
enable_patient_chat: bool = False

# Default chat models.
ai_model: str = "gemini-2.5-pro"
ai_model_flash: str = "gemini-2.5-flash-lite"
```

Future Phase-3b settings (planned, not landed):

```python
chat_quota_enforced: bool = False             # off by default in OSS
chat_quota_input_tokens_per_period: int | None = None
chat_quota_output_tokens_per_period: int | None = None
chat_quota_period_days: int = 30
```

---

## §16. Tests + invariants

The behaviors below are pinned by tests under `backend/tests/`. Any change
that breaks one is a deliberate amendment of this doc, not a green-light
refactor.

### §16.1 Lifecycle (Phase 1)

- `test_routes_chat.py::TestCreateConversation`:
  - Authorized patient → 201 with conversation row.
  - Missing patient or cross-user patient → 404 (never 403).
  - System prompt > 16,384 chars → 422.
  - Omitted title seeded from patient display name.
- `TestGetConversation`: unknown id → 404; valid → detail w/ messages.
- `TestListConversations`: archived excluded by default; `caller_feature_key`
  filter honored.
- `TestUpdateConversation`: title mutable; `archive=true` sets `archived_at`;
  `archive=false` clears it.
- `TestDeleteConversation`: default `mode` is `purge`; `mode=archive` is the
  soft-delete synonym.

### §16.2 Streaming + turn service (Phase 3)

- `test_routes_chat_streaming.py::TestSendMessage`:
  - Happy path: `meta` → `delta`* → `done`.
  - Per-message model override threads through to gateway.
  - Resolver override (DI swap) takes effect.
  - Archived conversation → 409.
  - Cross-user conversation → 404 (not 403).
  - Safety block emits `event: error` with `safety_block`.
  - Request body validation: non-empty, ≤ 32,768 chars.
- `test_chat_turn_service.py`:
  - User and assistant rows both persist.
  - `last_turn_at` bumped after assistant finalizes.
  - System prompt included in gateway call.
  - Safety block: error event emitted, `llm_finish_reason` persisted, not retried.
  - One retry on transient error with 1s backoff.
  - Two transient failures surface error.
  - Empty / whitespace-only message short-circuits.
  - Concurrent turn raises `TurnConcurrencyError`.

### §16.3 Context bundler (Phase 2)

- `test_chat_context_bundler.py`:
  - `estimate_tokens` rounds up; uses 4 chars/token.
  - Pasted-text overflow raises `ContextOverflowError`.
  - `progress_notes_recent` loads newest-first; respects `limit`.
  - `progress_notes_explicit` preserves caller order; ignores missing ids.
  - Document sources (intake, safety plan, treatment plan, medications) load
    most-recent; report `row_count=0` when patient has none.
  - Stub sources (`lab_values_recent`, `vitals_recent`) report
    `reason: module_not_available`.
  - Selection validation: unknown key raises `InvalidSelectionError`;
    falsy keys skipped.
  - Truncation: drops oldest progress notes first; drops low-priority
    sources before high-priority ones; under-budget assemblies keep
    everything.

---

## §17. Phase plan

| Phase  | Bead                                                  | Status        |
|--------|-------------------------------------------------------|---------------|
| 1      | THERAPY-tdh — conversation lifecycle (CRUD + audit)   | Shipped       |
| 2      | THERAPY-r3c — context bundler + manifest              | Shipped       |
| 3      | THERAPY-5x5 — streaming turn service + gateway + resolver | Shipped (PR pablo#159) |
| 3b     | LlmUsageMeter (THERAPY-bhv follow-up)                 | In progress (branch chat-phase3b-llm-usage-meter) |
| 4a     | THERAPY-q3e2 — this design doc                        | In progress   |
| 4      | THERAPY-q3z — ChatPanel baseline (§13.1–§13.3, §13.8–§13.12) | Open    |
| 4c     | THERAPY-0s44 — trust-affordance bundle (§13.4 + §13.6 + §13.7 + dev mount) | Open |
| 4d     | THERAPY-4wg3 — caller-supplied starter prompts (§13.5) | Open         |
| 5      | THERAPY-fbv9 — retention sweep + invariant check + PHI-in-logs test | Open |
| 6      | THERAPY-468a — operations docs + `.env.example` + release notes | Open  |

### §17.1 Phase 4 scope split

The Phase-4 frontend work is split across three implementation beads on top of
Bead 4a (this doc) to keep PRs reviewable:

- **THERAPY-q3z (Bead 4).** Baseline `ChatPanel.tsx`: §13.1 prop API, §13.2
  source chip rail, §13.3 per-message manifest disclosure, §13.8 error
  states, §13.9–§13.12 bubble/composer/archive/SSE. **No** briefing card,
  starter prompts, system-prompt view, scope footer, or dev mount.
- **THERAPY-0s44 (Bead 4c).** Trust-affordance bundle: §13.4 briefing card,
  §13.6 system-prompt view, §13.7 scope footer, plus a NODE_ENV-gated
  `/dev/chat` route for SSE/manifest dogfooding before a real caller
  integrates.
- **THERAPY-4wg3 (Bead 4d).** Caller-supplied starter prompts: §13.5
  `starterPrompts` prop + empty-state chip row.

The split is mechanical (one feature per PR) — there is no architectural
boundary between them. A reader of the final shipped code will see them as a
single coherent component.

---

## §18. Open decisions

These are deliberately deferred. Naming them here so the next maintainer
doesn't re-litigate from scratch.

- **Supervisor / shared access.** Today a conversation is owned by exactly
  one user. Multi-user access (a supervisor reviewing a trainee's chats,
  practice-wide consult threads) is a SaaS concern; OSS will not implement
  it unilaterally.
- **Retention policy specifics.** Phase 5 will pick concrete numbers
  (e.g. 90 days inactive → archive; 180 days archived → purge). Until then,
  retention is "whatever the operator's manual lifecycle calls do."
- **Mid-conversation starter-prompt reopener.** Re-scoping today is via
  free-typed instructions. If telemetry shows callers want a chip menu
  reachable from the composer, add it in Phase 6+.
- **Tool / function-calling.** Out of scope through Phase 6. Revisit when a
  caller workflow needs it (e.g. an "open this session" tool that drives
  navigation).

---

## §19. References

- `backend/app/routes/chat.py` — route layer.
- `backend/app/services/chat_service.py` — lifecycle business logic.
- `backend/app/services/chat_turn_service.py` — streaming turn orchestrator.
- `backend/app/services/chat_context_bundler.py` — source loaders + manifest.
- `backend/app/services/chat_llm_gateway.py` — Gemini gateway.
- `backend/app/services/chat_model_resolver.py` — model selection hook.
- `backend/app/db/models.py` — `ChatConversationRow`, `ChatMessageRow`.
- `backend/app/models/chat_api.py` — request/response Pydantic models.
- `backend/app/models/audit.py` — `CHAT_*` `AuditAction` values.
- `backend/alembic/versions/c4e9a7b3f180_chat_conversations_and_messages.py` — schema.
- `docs/HIPAA_AUDIT_LOGS.md` — audit-log conventions.
- APA Health Advisory on AI chatbots and mental health (May 2025).
- Frontiers in Digital Health, "Balancing risks and benefits: clinicians'
  perspectives on the use of generative AI chatbots in mental healthcare"
  (Mar 2025).
