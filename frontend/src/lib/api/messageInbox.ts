// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice's side of client messaging (`app.routes.patient_messages`,
 * clinician surface).
 *
 * Two views of one inbox. Conversations are grouped, one row per thread,
 * unread first, and carry no message text. Messages are ungrouped: every
 * message a client sent, one row each, newest first. Opening either lands in
 * the thread, which is where replying, closing and reopening happen.
 */

import { get, post } from "./client"

export type ThreadStatusFilter = "open" | "closed" | "all"

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

export interface InboxThread {
  id: string
  subject: string | null
  status: string
  created_at: string
  last_message_at: string
  closed_at: string | null
  assigned_user_id: string | null
  unread_count: number | null
  patient_id: string
  patient_name: string
}

export interface InboxThreadList {
  data: InboxThread[]
  total: number
  has_more: boolean
}

export interface InboxMessage extends ThreadMessage {
  patient_id: string
  patient_name: string
  thread_subject: string | null
  thread_status: string
  unread: boolean
}

export interface InboxMessageList {
  data: InboxMessage[]
  total: number
  has_more: boolean
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

export function listInboxThreads(status: ThreadStatusFilter, token?: string): Promise<InboxThreadList> {
  return get<InboxThreadList>(`${BASE}?status=${status}`, token)
}

export function listInboxMessages(unreadOnly: boolean, token?: string): Promise<InboxMessageList> {
  return get<InboxMessageList>(`${BASE}/messages?unread_only=${unreadOnly}`, token)
}

export function getUnreadThreadCount(token?: string): Promise<{ threads_with_unread: number }> {
  return get<{ threads_with_unread: number }>(`${BASE}/unread-count`, token)
}

export function getThread(threadId: string, token?: string): Promise<ThreadDetail> {
  return get<ThreadDetail>(`${BASE}/${threadId}`, token)
}

export function replyToThread(threadId: string, body: string, token?: string): Promise<ThreadMessage> {
  return post<ThreadMessage>(`${BASE}/${threadId}/replies`, { body }, token)
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
