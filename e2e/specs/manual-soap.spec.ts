/**
 * Manual SOAP authoring — end-to-end.
 *
 * The "New note" picker → empty-editor → save → finalize-without-rating
 * flow is the day-1 use case for a clinician who writes a note without
 * recording a session. This spec proves the API + serialization
 * boundary the editor depends on:
 *
 *   - A standalone SOAP note can be created empty, edited with content
 *     via PATCH content_edited, and finalized without a quality_rating.
 *     Re-reading the note shows the persisted edits + finalized_at.
 *   - A second finalize is rejected (409), same precondition as session
 *     notes.
 *
 * API-driven (not UI) because the failure modes we worry about here are
 * state-machine / serialization issues, not UI wiring.
 */
import { test, expect } from "../fixtures/auth";

import { createTestPatient } from "../flows/chat";

type StandaloneNote = {
  id: string;
  patient_id: string;
  session_id: string | null;
  note_type: string;
  content: Record<string, unknown> | null;
  content_edited: Record<string, unknown> | null;
  finalized_at: string | null;
  quality_rating: number | null;
};

async function authedGet<T>(
  apiUrl: string,
  idToken: string,
  path: string,
): Promise<T> {
  const resp = await fetch(`${apiUrl}${path}`, {
    headers: { Authorization: `Bearer ${idToken}` },
  });
  if (!resp.ok) {
    throw new Error(`GET ${path} failed: ${resp.status} ${await resp.text()}`);
  }
  return (await resp.json()) as T;
}

async function authedJson<T>(
  apiUrl: string,
  idToken: string,
  method: "POST" | "PATCH",
  path: string,
  body: Record<string, unknown>,
): Promise<{ status: number; json: T | null; text: string }> {
  const resp = await fetch(`${apiUrl}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${idToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  const text = await resp.text();
  let json: T | null = null;
  if (text) {
    try {
      json = JSON.parse(text) as T;
    } catch {
      // Non-JSON body — caller may still inspect text/status.
    }
  }
  return { status: resp.status, json, text };
}

test("manual SOAP: create empty → edit → save → finalize without rating @manual-soap", async ({
  enrolledUser,
}) => {
  const idToken = await enrolledUser.getIdToken();
  const apiUrl = enrolledUser.apiUrl;

  const patient = await createTestPatient({ apiUrl, idToken });

  // 1. Picker creates an empty SOAP row. SOAP is a core, always-allowed
  // note type, so this succeeds for any BAA-accepted user.
  const createResp = await authedJson<StandaloneNote>(
    apiUrl,
    idToken,
    "POST",
    `/api/patients/${patient.id}/notes`,
    { note_type: "soap" },
  );
  expect(createResp.status, `soap accepted (${createResp.text})`).toBe(201);
  expect(createResp.json!.note_type).toBe("soap");
  expect(
    createResp.json!.session_id,
    "manual notes have no session_id",
  ).toBeNull();
  expect(
    createResp.json!.content,
    "manual notes start with empty content",
  ).toBeNull();
  const noteId = createResp.json!.id;

  // 2. Clinician types into the empty form and saves via PATCH
  // content_edited. The four section keys mirror what
  // structuredToNarrative emits — the editor lives over them.
  const draft = {
    subjective: "**Chief Complaint:** Follow-up for anxiety.",
    objective: "**Appearance:** Well-groomed and engaged.",
    assessment: "**Clinical Impression:** Improvement since last visit.",
    plan: "**Next Session:** Two weeks.",
  };
  const patched = await authedJson<StandaloneNote>(
    apiUrl,
    idToken,
    "PATCH",
    `/api/notes/${noteId}`,
    { content_edited: draft },
  );
  expect(patched.status, `PATCH edits succeeded (${patched.text})`).toBe(200);
  expect(patched.json!.content_edited).toEqual(draft);

  // 3. The edits persist on a fresh GET.
  const reread = await authedGet<StandaloneNote>(
    apiUrl,
    idToken,
    `/api/notes/${noteId}`,
  );
  expect(reread.content_edited).toEqual(draft);
  expect(reread.finalized_at, "not finalized yet").toBeNull();

  // 4. Finalize WITHOUT a quality_rating — the rating wizard is hidden
  // for manual notes and the backend must accept the bare body.
  const finalized = await authedJson<StandaloneNote>(
    apiUrl,
    idToken,
    "POST",
    `/api/notes/${noteId}/finalize`,
    {},
  );
  expect(
    finalized.status,
    `finalize with no rating succeeds (${finalized.text})`,
  ).toBe(200);
  expect(finalized.json!.finalized_at, "finalized_at is now set").not.toBeNull();
  expect(
    finalized.json!.quality_rating,
    "no rating recorded for manual finalize",
  ).toBeNull();

  // 5. A second finalize is rejected — same precondition as session
  // notes (existing 409 behavior, regression-guarded here too).
  const reFinalize = await authedJson(
    apiUrl,
    idToken,
    "POST",
    `/api/notes/${noteId}/finalize`,
    {},
  );
  expect(reFinalize.status, "double finalize rejected").toBe(409);
});
