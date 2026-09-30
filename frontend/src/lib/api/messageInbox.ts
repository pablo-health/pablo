// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice's side of client messaging (`app.routes.patient_messages`,
 * clinician surface): one conversation, read and answered.
 *
 * Listing what clients wrote is the Inbox's job (`./inbox`), one item per
 * client message. Opening one lands in its thread, which is where replying,
 * closing and reopening happen.
 */

import { get, post } from "./client"

export interface MessageAttachment {
  document_id: string
  filename: string
  mime_type: string
  size_bytes: number
}

export interface ThreadMessage {
  id: string
  thread_id: string
  /** "patient" for the client; "clinician" or "practice" for the practice. */
  sender: string
  body: string
  created_at: string
  read_at: string | null
  attachments: MessageAttachment[]
}

/**
 * What a reply did in the Inbox. The message replied to is resolved; the
 * client's earlier unanswered messages are either offered (`earlier_open_ids`,
 * when the clinician is asked) or already marked handled
 * (`earlier_handled_ids`, when they chose "always"), depending on their
 * preference.
 */
export interface ReplyInboxOutcome {
  resolved_ids: string[]
  earlier_open_ids: string[]
  earlier_handled_ids: string[]
}

export interface ReplyMessage extends ThreadMessage {
  inbox?: ReplyInboxOutcome | null
}

export interface ThreadDetail {
  id: string
  subject: string | null
  status: string
  created_at: string
  last_message_at: string
  closed_at: string | null
  messages: ThreadMessage[]
}

const BASE = "/api/message-threads"

export function getThread(threadId: string, token?: string): Promise<ThreadDetail> {
  return get<ThreadDetail>(`${BASE}/${threadId}`, token)
}

export function replyToThread(
  threadId: string,
  body: string,
  inReplyToMessageId?: string,
  token?: string,
): Promise<ReplyMessage> {
  const payload = inReplyToMessageId ? { body, in_reply_to_message_id: inReplyToMessageId } : { body }
  return post<ReplyMessage>(`${BASE}/${threadId}/replies`, payload, token)
}

export function markThreadRead(threadId: string, token?: string): Promise<unknown> {
  return post<unknown>(`${BASE}/${threadId}/read`, {}, token)
}

export function closeThread(threadId: string, token?: string): Promise<unknown> {
  return post<unknown>(`${BASE}/${threadId}/close`, {}, token)
}

export function reopenThread(threadId: string, token?: string): Promise<unknown> {
  return post<unknown>(`${BASE}/${threadId}/reopen`, {}, token)
}
