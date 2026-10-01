// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The Inbox (`app.routes.inbox`): everything waiting on the clinician, in one
 * list.
 *
 * Each item is a view over its own source — a client message, a refill
 * request, a form handed in, a note to sign, a calendar change — so handling
 * it where it lives (answering the refill, signing the note) is what takes it
 * off the list. Dismiss, snooze and restore are the Inbox's own state, and
 * nothing is ever deleted: a handled item moves to Done and can come back.
 */

import { get, post } from "./client"

export type InboxView = "open" | "done"

export interface InboxItem {
  kind: string
  source_id: string
  patient_id: string | null
  patient_name: string | null
  title: string
  detail: string | null
  occurred_at: string
  severity: "normal" | "urgent"
  href: string
  /** What a kind's renderer needs beyond the fields above, e.g. `thread_id`. */
  context: Record<string, string>
  disposition: string | null
  resolved_at: string | null
  snoozed_until: string | null
}

export interface InboxList {
  data: InboxItem[]
  total: number
}

export interface HandledEarlier {
  handled_ids: string[]
}

const BASE = "/api/inbox"

function itemPath(kind: string, sourceId: string): string {
  return `${BASE}/${encodeURIComponent(kind)}/${encodeURIComponent(sourceId)}`
}

export function listInbox(view: InboxView, kinds?: string[], token?: string): Promise<InboxList> {
  const params = new URLSearchParams({ view })
  for (const kind of kinds ?? []) params.append("kinds", kind)
  return get<InboxList>(`${BASE}?${params.toString()}`, token)
}

export function getInboxCount(token?: string): Promise<{ count: number }> {
  return get<{ count: number }>(`${BASE}/count`, token)
}

export function dismissInboxItem(kind: string, sourceId: string, token?: string): Promise<InboxItem> {
  return post<InboxItem>(`${itemPath(kind, sourceId)}/dismiss`, {}, token)
}

export function snoozeInboxItem(
  kind: string,
  sourceId: string,
  until: Date,
  token?: string,
): Promise<InboxItem> {
  return post<InboxItem>(`${itemPath(kind, sourceId)}/snooze`, { until: until.toISOString() }, token)
}

export function restoreInboxItem(kind: string, sourceId: string, token?: string): Promise<InboxItem> {
  return post<InboxItem>(`${itemPath(kind, sourceId)}/restore`, {}, token)
}

/** Mark the client's still-open messages from before this one handled. */
export function handleEarlierMessages(messageId: string, token?: string): Promise<HandledEarlier> {
  return post<HandledEarlier>(`${itemPath("portal_message", messageId)}/handle-earlier`, {}, token)
}
