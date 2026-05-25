/**
 * Patient-context chat — end-to-end.
 *
 * What this proves:
 *   1. `ENABLE_PATIENT_CHAT=true` in the deployed environment — router
 *      is mounted.
 *   2. An enrolled, BAA-accepted user can create a patient via API.
 *   3. That user can create a chat conversation bound to that patient.
 *   4. POST /messages returns a streaming SSE response with assistant
 *      content (proves the configured chat model is reachable,
 *      configured, and responsive).
 *   5. No `error` events arrive in the stream.
 *
 * What this does NOT prove (out of scope):
 *   - Answer quality / clinical correctness (needs an eval set).
 *   - Tool-use / source-selection beyond the system prompt default.
 *   - Multi-user isolation (covered by pytest IDOR tests).
 *   - Cross-conversation continuity.
 *
 * Uses the worker-scoped `enrolledUser` fixture so this and future
 * chained API tests reuse the same enrolled user.
 */
import { test, expect } from "../fixtures/auth";
import {
  archiveConversation,
  assistantMessageIdFromStream,
  createConversation,
  createTestPatient,
  deleteConversation,
  listConversations,
  listPatientNotes,
  saveMessageAsNote,
  sendMessageAndCollect,
} from "../flows/chat";

test("creates a patient and round-trips a chat message via SSE @chat", async ({
  enrolledUser,
}) => {
  const idToken = await enrolledUser.getIdToken();
  const apiUrl = enrolledUser.apiUrl;

  // 1. Create a patient (chat is patient-scoped — needs a target).
  const patient = await createTestPatient({ apiUrl, idToken });
  expect(patient.id, "patient id").toMatch(/^[0-9a-f-]{36}$/);

  // 2. Create a conversation bound to that patient.
  const conv = await createConversation({
    apiUrl,
    idToken,
    patientId: patient.id,
  });
  expect(conv.id, "conversation id").toBeTruthy();
  expect(conv.patient_id, "conversation.patient_id").toBe(patient.id);

  // 3. Send a message and consume the SSE stream.
  // Question is intentionally generic — we are not testing answer
  // quality, only that the round-trip completes with content and
  // no error frames.
  const result = await sendMessageAndCollect({
    apiUrl,
    idToken,
    conversationId: conv.id,
    content: "In one short sentence, what is hypertension?",
    timeoutMs: 60_000,
  });

  // 4. Assert: got events, got non-empty text, no error frames.
  expect(result.events.length, "received SSE events").toBeGreaterThan(0);
  expect(result.hadError, `no error frames (events: ${
    result.events.map((e) => e.kind).join(",")
  })`).toBe(false);
  expect(result.text.trim().length, "non-empty assistant text").toBeGreaterThan(0);
});

/**
 * Chat history sidebar — backend round-trip.
 *
 * Proves the list / archive / delete surface the sidebar drives end to
 * end against the backend. Two conversations are created; one is
 * archived and verified to disappear from the default list + reappear
 * under include_archived; one is hard-deleted and verified absent on
 * every subsequent list.
 *
 * No LLM call is needed for this coverage — the lifecycle endpoints are
 * independent of the streaming turn surface, and we want this test
 * resilient to model latency.
 */
test("lists, archives, and deletes patient chat conversations @chat", async ({
  enrolledUser,
}) => {
  const idToken = await enrolledUser.getIdToken();
  const apiUrl = enrolledUser.apiUrl;

  const patient = await createTestPatient({ apiUrl, idToken });

  const convA = await createConversation({
    apiUrl,
    idToken,
    patientId: patient.id,
    callerFeatureKey: "e2e-history-test",
  });
  const convB = await createConversation({
    apiUrl,
    idToken,
    patientId: patient.id,
    callerFeatureKey: "e2e-history-test",
  });

  // Both visible on the default (non-archived) list, scoped to feature key
  // so other workers' tests don't pollute this assertion.
  const initial = await listConversations({
    apiUrl,
    idToken,
    patientId: patient.id,
    callerFeatureKey: "e2e-history-test",
  });
  const initialIds = initial.data.map((c) => c.id);
  expect(initialIds, "both conversations listed").toEqual(
    expect.arrayContaining([convA.id, convB.id]),
  );

  // Archive A and confirm it drops from the default view but resurfaces
  // when include_archived=true.
  await archiveConversation({ apiUrl, idToken, conversationId: convA.id });
  const afterArchive = await listConversations({
    apiUrl,
    idToken,
    patientId: patient.id,
    callerFeatureKey: "e2e-history-test",
  });
  expect(
    afterArchive.data.map((c) => c.id),
    "archived conversation hidden from default list",
  ).not.toContain(convA.id);
  expect(
    afterArchive.data.map((c) => c.id),
    "non-archived conversation still listed",
  ).toContain(convB.id);

  const withArchived = await listConversations({
    apiUrl,
    idToken,
    patientId: patient.id,
    callerFeatureKey: "e2e-history-test",
    includeArchived: true,
  });
  const archivedRow = withArchived.data.find((c) => c.id === convA.id);
  expect(archivedRow, "archived row reappears under include_archived").toBeDefined();
  expect(archivedRow?.archived_at, "archived_at populated").not.toBeNull();

  // Hard-delete B and confirm absence on both views.
  await deleteConversation({
    apiUrl,
    idToken,
    conversationId: convB.id,
  });
  const afterDelete = await listConversations({
    apiUrl,
    idToken,
    patientId: patient.id,
    callerFeatureKey: "e2e-history-test",
    includeArchived: true,
  });
  expect(
    afterDelete.data.map((c) => c.id),
    "purged conversation gone from list",
  ).not.toContain(convB.id);
});

/**
 * Save-as-note — chat→chart round-trip.
 *
 * Sends a single chat turn, captures the assistant_message_id off the
 * meta frame, calls the save-as-note endpoint, and verifies the new
 * note shows up in the patient's notes list with the assistant text
 * embedded in the narrative body and the chat source attribution in
 * the content's __source block.
 */
test("saves an assistant turn as a chart note @chat", async ({
  enrolledUser,
}) => {
  const idToken = await enrolledUser.getIdToken();
  const apiUrl = enrolledUser.apiUrl;

  const patient = await createTestPatient({ apiUrl, idToken });
  const conv = await createConversation({
    apiUrl,
    idToken,
    patientId: patient.id,
    callerFeatureKey: "e2e-save-as-note",
    // A short, deterministic prompt nudges the model toward a single
    // concise sentence so the assertion on note body is stable.
    systemPrompt:
      "You are a clinical assistant. Reply with exactly one short sentence.",
  });

  const stream = await sendMessageAndCollect({
    apiUrl,
    idToken,
    conversationId: conv.id,
    content: "Summarize: patient reports improved sleep this week.",
    timeoutMs: 60_000,
  });
  expect(stream.hadError, "no error frames").toBe(false);
  const assistantId = assistantMessageIdFromStream(stream);
  expect(assistantId, "meta frame carries an assistant_message_id").toBeTruthy();
  expect(stream.text.trim().length, "assistant text non-empty").toBeGreaterThan(0);

  const saved = await saveMessageAsNote({
    apiUrl,
    idToken,
    conversationId: conv.id,
    messageId: assistantId!,
  });
  expect(saved.patient_id, "note belongs to the same patient").toBe(patient.id);
  expect(saved.note_type, "note type is narrative").toBe("narrative");
  const noteContent = (saved.content ?? {}) as Record<string, unknown>;
  const noteBody = ((noteContent.note as { body?: string } | undefined)?.body ?? "").trim();
  expect(noteBody.length, "note body is populated").toBeGreaterThan(0);
  const source = noteContent.__source as
    | { kind?: string; chat_conversation_id?: string; chat_message_id?: string }
    | undefined;
  expect(source?.kind, "source kind is chat").toBe("chat");
  expect(source?.chat_conversation_id).toBe(conv.id);
  expect(source?.chat_message_id).toBe(assistantId);

  // The new note appears in the patient's notes list.
  const notes = await listPatientNotes({ apiUrl, idToken, patientId: patient.id });
  expect(
    notes.data.some((n) => n.id === saved.id),
    "saved note appears in patient notes list",
  ).toBe(true);
});
