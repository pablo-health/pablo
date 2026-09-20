// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient-side client for secure messaging with the practice.
 *
 * Deliberately a bare `fetch` rather than the clinician API client. The
 * clinician client resolves its credential from the signed-in clinician's
 * auth provider; the patient portal has no such user. It holds a short
 * lived patient session token and hands it to whatever calls a route on
 * the patient's behalf, so every function here takes that token as its
 * first argument and sends it as `Authorization: Bearer …`.
 *
 * Nothing here takes a patient id. The routes derive it from the token,
 * so there is no field for a caller to put the wrong value in.
 *
 * Failures carry a status and nothing else. A message body is the most
 * sensitive thing on this surface, and an error that quoted one would put
 * it into console output and error reporting, where it does not belong.
 */

import { buildApiUrl } from "@/lib/api/client"

const BASE = "/api/patient/messages"

/**
 * Who wrote a message. `practice` is the practice speaking for itself
 * rather than a person — an automatic acknowledgement, for instance. It
 * reads as the practice side of the conversation, not a third voice.
 */
export type PatientMessageSender = "patient" | "clinician" | "practice"

export interface PatientMessage {
  id: string
  thread_id: string
  sender: PatientMessageSender
  body: string
  created_at: string
  read_at?: string | null
}

export interface PatientMessageThread {
  id: string
  subject?: string | null
  status: string
  created_at: string
  last_message_at: string
  /** Filled in only on the patient's own list. */
  unread_count?: number | null
}

export interface PatientMessageThreadDetail extends PatientMessageThread {
  messages: PatientMessage[]
}

export interface PatientMessageThreadList {
  data: PatientMessageThread[]
  total: number
}

export interface MarkThreadReadResult {
  marked_read: number
}

/**
 * What the practice has configured about replies. The route that serves
 * this does not exist in every deployment yet, so the absence of an
 * answer is an ordinary outcome, not a failure — see
 * {@link getMessagingSettings}.
 */
export interface PatientMessagingSettings {
  sla_text?: string | null
}

/** A failed request. Carries the status and no part of the payload. */
export class PatientMessagesError extends Error {
  constructor(public status: number) {
    super(`Patient messaging request failed (${status})`)
    this.name = "PatientMessagesError"
  }
}

function headers(sessionToken: string): Record<string, string> {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${sessionToken}`,
  }
}

async function request<T>(
  sessionToken: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(buildApiUrl(`${BASE}${path}`), {
    ...init,
    headers: headers(sessionToken),
  })
  if (!response.ok) throw new PatientMessagesError(response.status)
  return (await response.json()) as T
}

/** Start a thread with its first message. */
export async function startThread(
  sessionToken: string,
  input: { subject?: string | null; body: string },
): Promise<PatientMessageThreadDetail> {
  return request<PatientMessageThreadDetail>(sessionToken, "/threads", {
    method: "POST",
    body: JSON.stringify({
      subject: input.subject?.trim() ? input.subject.trim() : null,
      body: input.body,
    }),
  })
}

/** Send into a thread the patient already has. */
export async function sendMessage(
  sessionToken: string,
  threadId: string,
  body: string,
): Promise<PatientMessage> {
  return request<PatientMessage>(
    sessionToken,
    `/threads/${encodeURIComponent(threadId)}/messages`,
    { method: "POST", body: JSON.stringify({ body }) },
  )
}

/** The patient's threads, with their unread counts. */
export async function listThreads(
  sessionToken: string,
): Promise<PatientMessageThreadList> {
  return request<PatientMessageThreadList>(sessionToken, "/threads")
}

/** One thread with its messages. */
export async function getThread(
  sessionToken: string,
  threadId: string,
): Promise<PatientMessageThreadDetail> {
  return request<PatientMessageThreadDetail>(
    sessionToken,
    `/threads/${encodeURIComponent(threadId)}`,
  )
}

/** Mark what the practice sent as read. */
export async function markThreadRead(
  sessionToken: string,
  threadId: string,
): Promise<MarkThreadReadResult> {
  return request<MarkThreadReadResult>(
    sessionToken,
    `/threads/${encodeURIComponent(threadId)}/read`,
    { method: "POST" },
  )
}

/**
 * What the practice has configured about replies, or null when this
 * deployment does not serve it.
 *
 * A 404 is the expected answer where the settings route has not been
 * added, so it resolves to null and the caller renders its own default.
 * Anything else still throws: a 500 means the practice may have
 * configured something the patient is not being shown.
 */
export async function getMessagingSettings(
  sessionToken: string,
): Promise<PatientMessagingSettings | null> {
  const response = await fetch(buildApiUrl(`${BASE}/settings`), {
    headers: headers(sessionToken),
  })
  if (response.status === 404) return null
  if (!response.ok) throw new PatientMessagesError(response.status)
  return (await response.json()) as PatientMessagingSettings
}
