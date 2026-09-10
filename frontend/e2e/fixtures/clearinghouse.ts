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
  /**
   * Set on a document upload instead of a body. The bytes are a signed
   * practice document; the fake records that they arrived and how many, and
   * keeps none of them.
   */
  bytes?: number
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
  /**
   * The exception class when the post never completed — the class, not the
   * message: an httpx error carries the request it failed on, and that
   * request is a signed webhook.
   */
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

  /**
   * Claim submissions, optionally for one control number.
   *
   * Matches both paths on purpose. Submission moved to the vendor's native
   * endpoint (`/professional-claim-submissions`) and the older compatibility
   * path (`/professionalclaims/v3/submission`) is still served, so a spec
   * that asked about only one of them would answer "nothing was sent" for a
   * claim that was.
   */
  async submissions(controlNumber?: string): Promise<ReceivedRequest[]> {
    const log = await this.received()
    return log.requests.filter(
      (r) =>
        (r.path.endsWith("/professional-claim-submissions") ||
          r.path.endsWith("/professionalclaims/v3/submission")) &&
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
