/**
 * SOAP from uploaded transcript — end-to-end.
 *
 * Exercises the day-1 transcript-upload happy path:
 *
 *   POST /api/patients/{patient_id}/sessions/upload
 *     body: { patient_id, session_date, transcript: { format, content } }
 *
 * The route (`backend/app/routes/sessions.py:upload_session`) creates
 * the session, runs the SOAP-generation pipeline synchronously, and
 * returns a SessionResponse with the generated note embedded under
 * `note`. No polling needed — that's a deliberate API-shape decision
 * we want to keep working.
 *
 * API-driven for the same reason as `manual-soap.spec.ts`: the failure
 * modes we worry about are state-machine / authorizer / serialization
 * boundary issues, not UI wiring. Quality of the generated SOAP is an
 * eval-harness concern (backend/evals/), not e2e.
 *
 * Uses worker-scoped `enrolledUser` (MFA + BAA).
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { test, expect } from "../fixtures/auth";

import { createTestPatient } from "../flows/chat";

const TRANSCRIPTS_DIR = path.resolve(
  __dirname,
  "..",
  "fixtures",
  "files",
  "transcripts",
);

function loadTranscript(name: "intake_short.txt" | "followup_short.txt"): string {
  return readFileSync(path.join(TRANSCRIPTS_DIR, name), "utf-8");
}

type TranscriptModel = { format: string; content: string };

type EmbeddedNote = {
  id: string;
  patient_id: string;
  session_id: string | null;
  note_type: string;
  content: Record<string, unknown> | null;
  content_edited: Record<string, unknown> | null;
  finalized_at: string | null;
  quality_rating: number | null;
};

type SessionResponse = {
  id: string;
  patient_id: string;
  patient_name: string;
  session_date: string;
  status: string;
  transcript: TranscriptModel;
  note: EmbeddedNote | null;
};

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
      // Non-JSON body — caller inspects status/text directly.
    }
  }
  return { status: resp.status, json, text };
}

function assertSoapSectionsPresent(content: Record<string, unknown>) {
  // SOAP draft must surface all four sections with at least one
  // populated field. The persisted shape is the SOAPNote dataclass dict
  // (see backend/app/models/soap_note.py:SOAPNote.to_dict and
  // backend/app/services/note_generation_service.py:_coerce_content_to_soap_note):
  //
  //   {
  //     subjective: { chief_complaint: { text, source_segment_ids }, ... },
  //     objective:  { appearance, behavior, ... },
  //     assessment: { clinical_impression, progress, ... },
  //     plan:       { interventions_used: [{text}, ...] | null, ... },
  //   }
  //
  // Each scalar field is a SOAPSentence dict ({text, source_segment_ids});
  // list fields are arrays of SOAPSentence dicts (or null when empty).
  for (const key of ["subjective", "objective", "assessment", "plan"]) {
    const val = content[key];
    expect(val, `SOAP section '${key}' is present`).toBeDefined();
    expect(
      typeof val === "object" && val !== null && !Array.isArray(val),
      `SOAP section '${key}' is an object (got ${JSON.stringify(val)?.slice(0, 80)})`,
    ).toBe(true);
    expect(
      sectionHasContent(val as Record<string, unknown>),
      `SOAP section '${key}' has at least one populated field ` +
        `(got ${JSON.stringify(val)?.slice(0, 120)})`,
    ).toBe(true);
  }
}

/**
 * True if any field on the section has non-empty text. Scalars are
 * SOAPSentence dicts ({text, source_segment_ids}); list fields are
 * arrays of SOAPSentence dicts.
 */
function sectionHasContent(section: Record<string, unknown>): boolean {
  for (const value of Object.values(section)) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value)) {
      for (const item of value) {
        if (sentenceHasText(item)) return true;
      }
      continue;
    }
    if (sentenceHasText(value)) return true;
  }
  return false;
}

function sentenceHasText(v: unknown): boolean {
  return (
    typeof v === "object" &&
    v !== null &&
    typeof (v as { text?: unknown }).text === "string" &&
    (v as { text: string }).text.trim().length > 0
  );
}

test("upload-session: synthetic intake transcript drafts a SOAP note end-to-end @soap-from-transcript", async ({
  enrolledUser,
}) => {
  const idToken = await enrolledUser.getIdToken();
  const apiUrl = enrolledUser.apiUrl;

  const patient = await createTestPatient({ apiUrl, idToken });

  // 1. Upload the synthetic CBT-I intake transcript. The route runs
  // SOAP generation synchronously and returns the embedded note.
  const upload = await authedJson<SessionResponse>(
    apiUrl,
    idToken,
    "POST",
    `/api/patients/${patient.id}/sessions/upload`,
    {
      patient_id: patient.id,
      session_date: new Date().toISOString(),
      transcript: {
        format: "txt",
        content: loadTranscript("intake_short.txt"),
      },
    },
  );

  expect(
    upload.status,
    `upload-session succeeded (${upload.status}: ${upload.text.slice(0, 200)})`,
  ).toBe(201);

  const session = upload.json!;
  expect(session.patient_id).toBe(patient.id);
  expect(session.transcript.format).toBe("txt");

  // 2. The pipeline produced a SOAP draft on the same response. We
  // don't assert the *quality* of the content here (that's evals);
  // we assert the four canonical sections are present and non-empty,
  // which is the contract the editor depends on. A regression that
  // returns `note: null` or a half-populated SOAP would surface here.
  expect(session.note, "embedded note returned").not.toBeNull();
  const note = session.note!;
  expect(note.note_type).toBe("soap");
  expect(note.session_id).toBe(session.id);
  expect(note.content, "SOAP content populated").not.toBeNull();
  assertSoapSectionsPresent(note.content!);
  expect(note.finalized_at, "draft is not finalized yet").toBeNull();

  // 3. Finalize the session with a quality rating — same path the
  // real "finalize" button drives. This proves the embedded note id
  // round-trips through the finalize handler.
  const finalized = await authedJson<SessionResponse>(
    apiUrl,
    idToken,
    "PATCH",
    `/api/sessions/${session.id}/finalize`,
    { quality_rating: 4 },
  );
  expect(
    finalized.status,
    `finalize succeeded (${finalized.status}: ${finalized.text.slice(0, 200)})`,
  ).toBe(200);
  expect(finalized.json!.status).toBe("finalized");
  expect(finalized.json!.note!.finalized_at, "finalized_at recorded").not.toBeNull();
  expect(finalized.json!.note!.quality_rating).toBe(4);
});

test("upload-session: invalid transcript format is rejected at the validation boundary @soap-from-transcript", async ({
  enrolledUser,
}) => {
  const idToken = await enrolledUser.getIdToken();
  const apiUrl = enrolledUser.apiUrl;

  const patient = await createTestPatient({ apiUrl, idToken });

  // The TranscriptFormat enum (backend/app/models/enums.py) accepts
  // exactly {vtt, json, txt, google_meet}. Anything else must 422 at
  // the Pydantic gate — never reach the SOAP pipeline. If this flips,
  // a misconfigured client could push arbitrary bytes through into
  // the LLM context.
  const bad = await authedJson<{ detail?: unknown }>(
    apiUrl,
    idToken,
    "POST",
    `/api/patients/${patient.id}/sessions/upload`,
    {
      patient_id: patient.id,
      session_date: new Date().toISOString(),
      transcript: {
        format: "exe",
        content: "irrelevant",
      },
    },
  );

  expect(
    bad.status,
    `invalid format rejected (got ${bad.status}: ${bad.text.slice(0, 200)})`,
  ).toBe(422);
});
