/**
 * API-level helpers for the patient-context chat round-trip. These are
 * pure fetch calls — no browser, no Playwright page object. The chat
 * spec uses these against a real backend with a worker-scoped enrolled
 * user.
 *
 * Endpoints exercised (pablo backend/app/routes/chat.py):
 *   POST /api/patients                                — create patient
 *   POST /api/chat/conversations                      — create conversation
 *   POST /api/chat/conversations/{id}/messages        — SSE stream
 *
 * All callers must hold an idToken from a user that has accepted the
 * BAA (chat is gated by `require_baa_acceptance`).
 */
import { randomBytes } from "node:crypto";

type Json = Record<string, unknown>;

async function postJson<T = Json>(args: {
  apiUrl: string;
  idToken: string;
  path: string;
  body: Json;
}): Promise<T> {
  const resp = await fetch(`${args.apiUrl}${args.path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${args.idToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(args.body),
  });
  if (!resp.ok) {
    throw new Error(
      `POST ${args.path} failed: ${resp.status} ${await resp.text()}`,
    );
  }
  return (await resp.json()) as T;
}

export type CreatedPatient = { id: string; first_name: string; last_name: string };

export async function createTestPatient(args: {
  apiUrl: string;
  idToken: string;
}): Promise<CreatedPatient> {
  // State-pollution strategy: unique name per call, tolerate accumulated
  // rows in the worker's account. The 8-hex suffix makes collisions
  // astronomically unlikely without a teardown helper.
  const noise = randomBytes(4).toString("hex");
  return postJson<CreatedPatient>({
    apiUrl: args.apiUrl,
    idToken: args.idToken,
    path: "/api/patients",
    body: { first_name: "ChatTest", last_name: noise },
  });
}

export type CreatedConversation = {
  id: string;
  patient_id: string;
  caller_feature_key: string;
};

export async function createConversation(args: {
  apiUrl: string;
  idToken: string;
  patientId: string;
  callerFeatureKey?: string;
  systemPrompt?: string;
}): Promise<CreatedConversation> {
  return postJson<CreatedConversation>({
    apiUrl: args.apiUrl,
    idToken: args.idToken,
    path: "/api/chat/conversations",
    body: {
      patient_id: args.patientId,
      caller_feature_key: args.callerFeatureKey ?? "e2e-chat-test",
      caller_system_prompt:
        args.systemPrompt ??
        "You are a clinical assistant. Reply concisely in one sentence.",
    },
  });
}

export type ChatStreamEvent = { kind: string; data: Json };

export type ChatStreamResult = {
  events: ChatStreamEvent[];
  /** Concatenation of all text fragments seen in `delta`/`text`/`chunk` events. */
  text: string;
  /** True if any event had kind === "error". */
  hadError: boolean;
};

/**
 * Send a user message and consume the entire SSE stream. Parses the
 * standard `event: <kind>\ndata: <json>\n\n` framing emitted by
 * StreamingResponse in chat.py's send_message. Returns once the
 * stream closes.
 */
export async function sendMessageAndCollect(args: {
  apiUrl: string;
  idToken: string;
  conversationId: string;
  content: string;
  timeoutMs?: number;
}): Promise<ChatStreamResult> {
  const ctrl = new AbortController();
  const timeoutMs = args.timeoutMs ?? 60_000;
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);

  let resp: Response;
  try {
    resp = await fetch(
      `${args.apiUrl}/api/chat/conversations/${args.conversationId}/messages`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${args.idToken}`,
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify({ content: args.content }),
        signal: ctrl.signal,
      },
    );
  } catch (err) {
    clearTimeout(timer);
    throw err;
  }

  if (!resp.ok) {
    clearTimeout(timer);
    throw new Error(
      `send_message failed: ${resp.status} ${await resp.text()}`,
    );
  }
  if (!resp.body) {
    clearTimeout(timer);
    throw new Error("send_message returned no body");
  }

  const events: ChatStreamEvent[] = [];
  let text = "";
  let hadError = false;

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE frames are separated by a blank line ("\n\n").
      let sep: number;
      while ((sep = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const ev = parseSseFrame(frame);
        if (!ev) continue;
        events.push(ev);
        if (ev.kind === "error") hadError = true;
        // Text accumulators: chat.py uses event kinds defined in
        // ChatTurnService. Cover the common content kinds defensively
        // — we only assert "got SOME text", so over-collecting is fine.
        const maybeText =
          (ev.data as { text?: string; delta?: string; content?: string }).text ??
          (ev.data as { delta?: string }).delta ??
          (ev.data as { content?: string }).content;
        if (typeof maybeText === "string") text += maybeText;
      }
    }
  } finally {
    clearTimeout(timer);
    reader.releaseLock();
  }

  return { events, text, hadError };
}

// ---------------------------------------------------------------------------
// Conversation lifecycle helpers (history sidebar coverage)
// ---------------------------------------------------------------------------

async function getJson<T>(args: {
  apiUrl: string;
  idToken: string;
  path: string;
}): Promise<T> {
  const resp = await fetch(`${args.apiUrl}${args.path}`, {
    method: "GET",
    headers: { Authorization: `Bearer ${args.idToken}` },
  });
  if (!resp.ok) {
    throw new Error(
      `GET ${args.path} failed: ${resp.status} ${await resp.text()}`,
    );
  }
  return (await resp.json()) as T;
}

async function patchJson<T>(args: {
  apiUrl: string;
  idToken: string;
  path: string;
  body: Json;
}): Promise<T> {
  const resp = await fetch(`${args.apiUrl}${args.path}`, {
    method: "PATCH",
    headers: {
      Authorization: `Bearer ${args.idToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(args.body),
  });
  if (!resp.ok) {
    throw new Error(
      `PATCH ${args.path} failed: ${resp.status} ${await resp.text()}`,
    );
  }
  return (await resp.json()) as T;
}

async function deleteRequest(args: {
  apiUrl: string;
  idToken: string;
  path: string;
}): Promise<void> {
  const resp = await fetch(`${args.apiUrl}${args.path}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${args.idToken}` },
  });
  if (!resp.ok && resp.status !== 204) {
    throw new Error(
      `DELETE ${args.path} failed: ${resp.status} ${await resp.text()}`,
    );
  }
}

export type ConversationRow = {
  id: string;
  patient_id: string;
  title: string;
  archived_at: string | null;
  last_turn_at: string | null;
};

export type ConversationList = { data: ConversationRow[]; total: number };

export async function listConversations(args: {
  apiUrl: string;
  idToken: string;
  patientId: string;
  callerFeatureKey?: string;
  includeArchived?: boolean;
}): Promise<ConversationList> {
  const qs = new URLSearchParams({ patient_id: args.patientId });
  if (args.callerFeatureKey) qs.set("caller_feature_key", args.callerFeatureKey);
  if (args.includeArchived) qs.set("include_archived", "true");
  return getJson<ConversationList>({
    apiUrl: args.apiUrl,
    idToken: args.idToken,
    path: `/api/chat/conversations?${qs.toString()}`,
  });
}

export async function archiveConversation(args: {
  apiUrl: string;
  idToken: string;
  conversationId: string;
}): Promise<ConversationRow> {
  return patchJson<ConversationRow>({
    apiUrl: args.apiUrl,
    idToken: args.idToken,
    path: `/api/chat/conversations/${args.conversationId}`,
    body: { archive: true },
  });
}

export async function deleteConversation(args: {
  apiUrl: string;
  idToken: string;
  conversationId: string;
  mode?: "purge" | "archive";
}): Promise<void> {
  const mode = args.mode ?? "purge";
  return deleteRequest({
    apiUrl: args.apiUrl,
    idToken: args.idToken,
    path: `/api/chat/conversations/${args.conversationId}?mode=${mode}`,
  });
}

// ---------------------------------------------------------------------------
// Save-as-note
// ---------------------------------------------------------------------------

export type SavedNote = {
  id: string;
  patient_id: string;
  note_type: string;
  content: Record<string, unknown> | null;
};

export async function saveMessageAsNote(args: {
  apiUrl: string;
  idToken: string;
  conversationId: string;
  messageId: string;
}): Promise<SavedNote> {
  return postJson<SavedNote>({
    apiUrl: args.apiUrl,
    idToken: args.idToken,
    path: `/api/chat/conversations/${args.conversationId}/messages/${args.messageId}/save-as-note`,
    body: {},
  });
}

export type PatientNote = {
  id: string;
  patient_id: string;
  note_type: string;
  content: Record<string, unknown> | null;
};

export async function listPatientNotes(args: {
  apiUrl: string;
  idToken: string;
  patientId: string;
}): Promise<{ data: PatientNote[]; total: number }> {
  return getJson({
    apiUrl: args.apiUrl,
    idToken: args.idToken,
    path: `/api/patients/${args.patientId}/notes`,
  });
}

/**
 * Pull the assistant message id out of a collected stream's meta frame.
 * The chat backend emits ``event: meta`` once per turn with both the
 * ``user_message_id`` and ``assistant_message_id`` — this is the only
 * place that id is surfaced before the conversation detail GET, so e2e
 * callers that want to save-as-note immediately need it.
 */
export function assistantMessageIdFromStream(
  result: ChatStreamResult,
): string | undefined {
  const meta = result.events.find((e) => e.kind === "meta");
  if (!meta) return undefined;
  const id = (meta.data as { assistant_message_id?: string }).assistant_message_id;
  return typeof id === "string" ? id : undefined;
}

function parseSseFrame(frame: string): ChatStreamEvent | undefined {
  const lines = frame.split("\n");
  let kind: string | undefined;
  let dataLine: string | undefined;
  for (const line of lines) {
    if (line.startsWith("event:")) kind = line.slice(6).trim();
    else if (line.startsWith("data:")) {
      dataLine = (dataLine ?? "") + line.slice(5).trim();
    }
  }
  if (!kind || dataLine === undefined) return undefined;
  let parsed: Json;
  try {
    parsed = JSON.parse(dataLine) as Json;
  } catch {
    parsed = { raw: dataLine };
  }
  return { kind, data: parsed };
}
