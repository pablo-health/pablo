// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Drive the fake clearinghouse (scripts/fake_clearinghouse.py): read what
 * it received, reset it between specs, and force the 277CA or 835 for a
 * claim instead of waiting on its timer.
 */

import { CLEARINGHOUSE_URL } from "./stack"

export interface ReceivedRequest {
  at: string
  method: string
  path: string
  query: Record<string, string>
  /** Lower-cased names; `authorization` is redacted. */
  headers: Record<string, string>
  json: unknown
  /** Set on claim submissions. */
  control_number: string | null
}

export interface WebhookDelivery {
  at: string
  kind: "277" | "835"
  control_number: string
  transaction_id: string
  event_id: string
  url: string
  /** The backend's response status, or null when the post never completed. */
  status: number | null
  error: string | null
}

export interface ReceivedLog {
  requests: ReceivedRequest[]
  webhooks: WebhookDelivery[]
  transactions: unknown[]
}

async function call<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${CLEARINGHOUSE_URL}${path}`, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    throw new Error(`fake clearinghouse ${method} ${path} → ${response.status}`)
  }
  return (await response.json()) as T
}

export const clearinghouse = {
  /** Everything received since the last reset. */
  received(): Promise<ReceivedLog> {
    return call<ReceivedLog>("GET", "/_fake/received")
  },

  /** Claim submissions, optionally for one control number. */
  async submissions(controlNumber?: string): Promise<ReceivedRequest[]> {
    const log = await this.received()
    return log.requests.filter(
      (r) =>
        r.path.endsWith("/professionalclaims/v3/submission") &&
        (controlNumber === undefined || r.control_number === controlNumber),
    )
  },

  /** Clear the log and cancel pending 277CA / 835 timers. */
  async reset(): Promise<void> {
    await call("POST", "/_fake/reset")
  },

  /** Deliver the 277CA or 835 for a control number now. */
  deliver(kind: "277" | "835", controlNumber: string): Promise<WebhookDelivery> {
    return call<WebhookDelivery>("POST", "/_fake/deliver", { kind, control_number: controlNumber })
  },
}
