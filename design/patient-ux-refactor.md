# Patient UX Refactor — Detail Page + List

## Goal

Make the patient detail page legible at a glance — visible recent notes, visible
and viewable documents, and chat promoted to a first-class modal — while
de-crowding the page. Separately, fix the patient list so clinicians with >20
patients can actually reach all of them and so search behaves the way they
expect.

## Non-goals

- **No backend model changes.** No new tables or migrations. Notes and Documents
  remain separate backend entities with their own repositories, routes, and
  audit events. Allowed: trivial query-param wiring on existing list routes
  (page_size, substring search).
- **No appointments / sessions / SOAP-generation redesign.** Those surfaces also
  hang off the patient, but they're out of scope here.
- **No mobile-specific work.** Desktop-first; mobile is acceptable if it falls
  out cleanly but gets no extra polish.
- **No new permission model.** `patient_clinicians` grants and the
  `psychotherapy_notes` / `therapist_private` lock semantics stay exactly as-is.

## Context

### Patient detail page today

`frontend/app/(dashboard)/dashboard/patients/[id]/page.tsx` renders, top to
bottom:

1. **Header** — back link + a `+ New Note` button
2. **Patient Info Card (Summary)** — inlined directly in `page.tsx`; name,
   email, phone, DOB, diagnosis, next session / appointment dates. *This is the
   part the user likes.*
3. **Notes "section"** — literally just
   `frontend/src/components/notes/NewNoteButton.tsx`: a `+ New note` button and a
   `View all notes` link. **There is no preview of existing notes at all.** A
   clinician cannot see what they've already written without navigating away.
4. **Documents** — `frontend/src/components/patients/PatientDocuments.tsx`: an
   upload form + a flat list with Download / Delete. **No in-app viewer** — the
   only way to read a document is to download it. Documents read as a forgotten
   footer; it's unclear one is even attached.
5. **`PatientChartExtras`** —
   `frontend/src/components/patients/PatientChartExtras.tsx`, an extension slot
   that returns `null` by default.

**Chat does not exist on the patient page at all.** It lives only at the dev
route `frontend/app/dev/chat/`. So "promote chat to a button" is really "bring
chat to the patient page for the first time, as a modal."

### Data + primitives already available

- **Notes hooks** (`frontend/src/hooks/useNotes.ts`): `usePatientNotes()` (list),
  `useNote()` (detail), `useCreateStandaloneNote()`.
  Note shape: `id, patient_id, session_id, note_type ("soap" | "narrative"),
  content, content_edited, finalized_at, quality_rating, export_status,
  created_at, updated_at, …`. Enough to preview: title/type badge, draft vs
  finalized (`finalized_at`), and a date.
- **Documents hooks** (`frontend/src/hooks/usePatientDocuments.ts`):
  `usePatientDocuments()`, `useUploadPatientDocument()`,
  `useDeletePatientDocument()`. The list response already carries
  `text_extraction_failed` (the "OCR not yet supported" badge) and `category`.
- **Document categories** (3, not 2):
  - `chart` — shared with co-treating clinicians, releasable to patient
  - `therapist_private` — uploader-only working material
  - `psychotherapy_notes` — uploader-only, HIPAA §164.501 carve-out, lock icon
- **Chat API** (`frontend/src/lib/chat/api.ts` + `sse.ts`): `createConversation`,
  `getConversation`, `updateConversation`, `streamChatMessages`. The streaming
  message UI already works at the dev route — it's a reuse, not a rebuild.
- **Dialog primitive**: shadcn / Radix `Dialog` at
  `frontend/src/components/ui/dialog.tsx`. Use this for the chat modal and the
  document viewer; do not introduce a new modal lib.

### Patient list today

`frontend/src/components/patients/PatientTable.tsx` calls
`usePatientList({ search })` with no `page_size` and renders no pagination
controls. Backend default is `page_size=20`
(`backend/app/routes/patients.py:163`), so rows 21+ are unreachable. Search
defaults to `search_by=last_name` and matches with
`startswith(search_lower)` prefix-only
(`backend/app/repositories/patient.py:206-208`); the placeholder
("Search patients by name…") implies more than it delivers, and a first-name
query silently returns nothing. Both pagination and search are tracked
follow-ups.

## Approach

### IA recommendation: hybrid (co-locate in the UI, keep separate underneath)

**Recommendation: do NOT merge Notes and Documents into one backend feed. DO
co-locate them visually under a single "Chart" section with tabs.**

Why not a true merge:

- They're created by **different workflows** — Notes via a write/finalize/export
  lifecycle (SOAP generation, EHR export status); Documents via upload → OCR.
  A merged model would have to model the union of both lifecycles.
- They have **different audit events and different access nuance.** Documents
  carry the HIPAA §164.501 psychotherapy-notes carve-out as a first-class
  category with a lock; Notes don't. A merged list risks flattening that signal
  — exactly the regression the user flagged ("combining can't lose the lock").
- The non-goal "no backend model changes" makes a real merge out of scope
  anyway.

Why co-locate visually:

- The user's actual complaint is **density and discoverability**, not data
  modeling. A `Chart` section with `Notes | Documents` tabs gives one obvious
  place to look, makes Documents as prominent as Notes, and preserves each
  component's distinct affordances and badges.
- Leaves the door open for a later **read-only merged timeline** tab (frontend
  join, no backend change) if users still want a single chronological view.
  Deferred as a follow-up, not built now.

### Patient detail page — target layout

```
┌─────────────────────────────────────────────────────────────┐
│  ← Patients            [ Chat ]   [ + New Note ]   [ ⋯ ]      │  header
├─────────────────────────────────────────────────────────────┤
│  PATIENT SUMMARY  (kept; extracted into <PatientSummary/>)    │
│  name · dx · next session · contact                           │
├─────────────────────────────────────────────────────────────┤
│  CHART                                                        │
│  ┌ Notes ─ Documents ─────────────────────────────────────┐  │
│  │  Notes tab (default):                                   │  │
│  │   • 3 most-recent notes, each: type badge, draft/final, │  │
│  │     date, first line; click → existing edit page        │  │
│  │   • "View all notes" link                               │  │
│  │   • + New Note (also still in header)                   │  │
│  │  Documents tab:                                         │  │
│  │   • upload control                                      │  │
│  │   • list w/ category badge (lock on psychotherapy/      │  │
│  │     private), OCR-status chip, View + Download + Delete │  │
│  │   • View → opens DocumentViewerSheet                    │  │
│  └─────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│  PatientChartExtras (unchanged slot)                          │
└─────────────────────────────────────────────────────────────┘
```

- **Chat** = header button → large Radix `Dialog` (~90vw × ~85vh) wrapping the
  **existing** `ChatPanelWithHistory` (already a two-column history-sidebar +
  active-thread layout — see `frontend/src/components/chat/`). The chat engine,
  SSE streaming, and API client are all already AGPL OSS. The mount passes **no
  `callerSystemPrompt`** so the backend resolves the prompt server-side
  (`backend/app/prompts/chat.py::get_chat_system_prompt`): OSS gets
  `DEFAULT_PROMPT`; a downstream deployment can register a proprietary
  provider-aware resolver via `register_provider`. Audit events already fire
  through the existing chat routes — the modal is a presentation change only.

  **Deployment note (OSS core + downstream deployments):**
  - *OSS adds:* `PatientChatDialog` + the Chat button + a neutral default
    source-selection (the current `DEFAULT_SELECTION` is not proprietary).
    Today OSS's `PatientChartExtras` returns `null` and the only chat mount is
    the dev route — this is the first real OSS mount.
  - *Downstream deployments:* a deployment's own inline chat mount is removed
    in favor of the shared OSS patient-page modal (so chat doesn't
    double-render once OSS owns it), along with any hardcoded proprietary
    system-prompt text in the frontend — that text is redundant with the
    backend resolver. Net: proprietary prompt text leaves the frontend
    entirely.
  - *Delete `frontend/app/dev/chat/`* in the OSS PR — referenced nowhere.
    Sequence: add the modal mount first (same PR), then delete the dev
    route, so OSS never loses chat access mid-change.
- **Document viewer** = a right-side `Sheet`/`Dialog` drawer. Render the signed
  URL with a native `<embed type="application/pdf">` for PDFs and `<img>` for
  PNG/JPEG. **No PDF.js initially** — browser-native rendering covers read-only
  viewing; revisit PDF.js only if we later need citation highlighting. The
  viewer fetches via the existing signed-download-URL path, so the existing
  document-access audit event still fires.

### Patient list — target

- **Pagination:** short-term, request `page_size: 100` (API max) so
  no one hits the cap in practice; surface the real `total` ("Showing N
  patients"). Add Prev/Next controls only if a practice exceeds 100 — noted as a
  follow-up, not built now.
- **Search:** switch the repo from `startswith` to substring
  (`contains`) across **both** first and last name; drop the unused `search_by`
  toggle; keep the placeholder generic ("Search patients…") now that behavior
  matches it. Backend + tests change in one place.

## Breakdown

All items are in the **pablo** repo (frontend unless noted). Grouped by phase;
within a phase, items are parallelizable.

### Phase 1 — groundwork (no user-visible regressions)

- **[pablo]** Extract `PatientSummary` component — pull the inlined summary out
  of `patients/[id]/page.tsx` into `components/patients/PatientSummary.tsx`.
  - depends on: none
  - acceptance: page renders identically; summary is a standalone component with
    props typed from `PatientResponse`.

- **[pablo]** Add `PatientChartTabs` shell — new
  `components/patients/PatientChartTabs.tsx` with `Notes | Documents` tabs
  (shadcn Tabs), empty bodies wired to the existing list hooks.
  - depends on: none
  - acceptance: tabs render on the patient page below the summary; switching
    tabs works; no content yet beyond placeholders.

- **[pablo]** Fix document download auth — the current
  `<a href="/api/documents/{id}/file">` 401s because anchor navigation can't
  send the Firebase bearer token. Return the signed URL via the authenticated
  fetch client, then navigate to GCS. **Establishes the contract the viewer
  reuses.**
  - depends on: none
  - acceptance: Download returns the file (all 3 categories); audit event still
    fires server-side; signed URL obtained via authed API client; no token in
    any URL.

- **[pablo]** Add `DocumentViewerSheet` — new
  `components/patients/DocumentViewerSheet.tsx`: a right drawer that takes a
  document id, obtains the signed URL via the **authenticated-fetch pattern
  from the document download auth fix**, renders `<embed>` (PDF) or `<img>`
  (image), with loading + error states.
  - depends on: Fix document download auth
  - acceptance: given a document id, the sheet opens and renders the file;
    document-access audit event still fires; closes cleanly.

- **[pablo]** Patient list pagination — request `page_size: 100`;
  display real `total`.
  - depends on: none
  - acceptance: a user with >20 patients sees all (≤100); "Showing N" visible;
    test covers the page_size.

- **[pablo]** Patient list search — substring match across both
  names in `backend/app/repositories/patient.py`; drop `search_by` from the UI
  path; keep generic placeholder.
  - depends on: none
  - acceptance: middle-fragment last-name query matches; first-name query
    matches without a toggle; repo + route tests updated.

### Phase 2 — assemble the page

- **[pablo]** Notes inline preview — fill the Notes tab: 3 most-recent notes via
  `usePatientNotes()`, each with type badge (SOAP/narrative), draft/finalized
  state (`finalized_at`), and date. **No content first-line** (per decision —
  metadata only). Click → existing edit page; keep "View all" + "+ New Note".
  - depends on: PatientChartTabs shell
  - acceptance: recent notes visible without navigating away; empty state when
    none; click routes to the correct note.

- **[pablo]** Documents in Chart tab — move `PatientDocuments` content into the
  Documents tab; add a **View** action that opens `DocumentViewerSheet`; ensure
  category badges + lock icon + OCR-status chip survive the move.
  - depends on: PatientChartTabs shell, DocumentViewerSheet
  - acceptance: upload/list/delete still work; lock icon on
    `psychotherapy_notes`/`therapist_private` preserved; View opens the viewer.

- **[pablo]** Chat modal (OSS core; downstream deployments update in step) —
  *OSS:* new `components/patients/PatientChatDialog.tsx` (Radix Dialog wrapping
  the existing `ChatPanelWithHistory`), header `Chat` button, neutral default
  source-selection, **no `callerSystemPrompt`** (backend resolves); delete
  `frontend/app/dev/chat/` in the same PR.
  *Downstream deployment:* a deployment's own inline chat mount and any
  hardcoded proprietary system-prompt text are removed (now redundant with the
  backend resolver).
  - depends on: PatientSummary extract (header refactor lands there)
  - acceptance: button opens modal; past conversations listed and selectable;
    new turn streams; OSS core uses `DEFAULT_PROMPT`, a downstream deployment
    uses its registered proprietary prompt (no frontend prompt); chat audit
    events fire; no double-render; dev route gone.

### Phase 3 — finish

- **[pablo]** Patient-page polish + verification — spacing/typography pass
  against brand tokens (cream/honey/sage, Fraunces headings, DM Sans body);
  confirm audit wiring across viewer + chat modal; add an e2e/integration smoke
  for the assembled page.
  - depends on: all Phase 2 items
  - acceptance: `make check` green; manual pass confirms notes preview, doc view,
    and chat modal on a seeded patient; no PHI in console/logs.

## Decisions (resolved)

- **IA** — hybrid: one "Chart" section, `Notes | Documents` tabs; backend stays
  separate; merged timeline deferred.
- **Notes preview** — 3 most-recent, metadata only (type badge, draft/finalized,
  date), **no content first-line**.
- **Document viewer** — native `<embed>` (PDF) / `<img>` (image); no PDF.js now.
- **Pagination** — `page_size=100` + a "Showing N" total; no Prev/Next controls
  yet.
- **Search** — substring across both names; drop the `search_by` toggle from the
  UI.
- **Chat ownership** — OSS owns the mount (button + modal); a downstream
  deployment drops its inline mount + frontend prompts; prompt resolution stays
  server-side.
- **Chat modal** — modal-only, **no** `?chat=` deep-link param.
- **Image viewer** — plain `<img>` fit-to-width; zoom/pan deferred.
- **Section name** — "Chart".

## Open questions

- **Empty/edge state copy** — patient with zero notes AND zero documents: what
  does each tab say? (Per-tab empty copy, not combined — needs wording during
  implementation.)

## Out of scope / follow-ups

- True merged **chronological timeline** tab (frontend join of notes +
  documents) — defer until users ask after seeing the tabbed version.
- **PDF.js** with citation highlighting — only if chat-to-document citation
  linking becomes a feature.
- **Prev/Next pagination controls** on the patient list — only if a practice
  exceeds 100 patients.
- The **`PatientChartExtras`** slot — left untouched; whatever it's reserved for
  is a separate effort.
