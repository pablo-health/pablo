// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Read the fake text-message gateway (scripts/fake_sms.py): what the stack has
 * sent, and the step-up code in a message — which is how a spec completes a
 * portal sign-in.
 *
 * ## Walking a portal sign-in from a spec
 *
 * Both factors are readable, and each from its own stand-in, because each
 * travels a different channel:
 *
 * - the magic link arrives as email, so it comes from the mail fixture:
 *   `firstLink(await mail.waitFor(patientEmail))` (see `mail.ts`);
 * - the step-up code arrives as a text, so it comes from here:
 *   `stepUpCode(await sms.waitFor(patientPhone))`.
 *
 * Post both to `/api/patient/auth/redeem` (or open the link and type the code
 * into the page) and the response carries the patient's session token.
 *
 * The compose stack wires this up with `PORTAL_SMS_GATEWAY=capture` and
 * `PORTAL_SMS_CAPTURE_URL`; the backend gateway that posts here refuses to
 * exist outside a development environment, so nothing about this reaches a
 * deployment that serves real people.
 */

import { SMS_URL } from "./stack"

export interface CapturedSms {
  at: string
  to: string
  body: string
}

async function call<T>(method: "GET" | "POST", path: string): Promise<T> {
  const response = await fetch(`${SMS_URL}${path}`, { method })
  if (!response.ok) {
    throw new Error(`fake sms ${method} ${path} → ${response.status}`)
  }
  return (await response.json()) as T
}

export const sms = {
  /** Everything received since the last reset, oldest first. */
  async received(): Promise<CapturedSms[]> {
    return (await call<{ messages: CapturedSms[] }>("GET", "/_fake/messages")).messages
  },

  /** Drop every captured message. */
  async reset(): Promise<void> {
    await call("POST", "/_fake/reset")
  },

  /**
   * The newest message to `number`, waited for — the send happens on the
   * request thread, but the capture is a second process away.
   */
  async waitFor(number: string, timeoutMs = 10_000): Promise<CapturedSms> {
    const deadline = Date.now() + timeoutMs
    for (;;) {
      const matching = (await this.received()).filter((m) => m.to === number)
      const newest = matching.at(-1)
      if (newest !== undefined) return newest
      if (Date.now() >= deadline) {
        throw new Error(`no message for ${number} within ${timeoutMs}ms`)
      }
      await new Promise((resolve) => setTimeout(resolve, 250))
    }
  },
}

/** The six-digit step-up code in a message body. */
export function stepUpCode(message: CapturedSms): string {
  const found = message.body.match(/\b(\d{6})\b/)
  if (found === null) throw new Error(`no step-up code in "${message.body}"`)
  return found[1]
}
