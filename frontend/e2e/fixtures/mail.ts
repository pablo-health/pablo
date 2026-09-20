// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Read the fake mail server (scripts/fake_mail.py): what the stack has sent,
 * and the first link in a message — which is how a spec follows a mail the
 * product expects a person to click.
 *
 * A portal invitation is one of those mails, and it is half of a two-factor
 * sign-in: this side carries the magic link, and the step-up code arrives on
 * the other channel. `sms.ts` reads that one, and its header walks the whole
 * sign-in from a spec.
 */

import { MAIL_URL } from "./stack"

export interface CapturedEmail {
  at: string
  from: string
  to: string[]
  subject: string
  text: string
}

async function call<T>(method: "GET" | "POST", path: string): Promise<T> {
  const response = await fetch(`${MAIL_URL}${path}`, { method })
  if (!response.ok) {
    throw new Error(`fake mail ${method} ${path} → ${response.status}`)
  }
  return (await response.json()) as T
}

export const mail = {
  /** Everything received since the last reset, oldest first. */
  async received(): Promise<CapturedEmail[]> {
    return (await call<{ messages: CapturedEmail[] }>("GET", "/_fake/messages")).messages
  },

  /** Drop every captured message. */
  async reset(): Promise<void> {
    await call("POST", "/_fake/reset")
  },

  /**
   * The newest message to `address`, waited for — the send happens on the
   * request thread, but the capture is a second process away.
   */
  async waitFor(address: string, timeoutMs = 10_000): Promise<CapturedEmail> {
    const deadline = Date.now() + timeoutMs
    for (;;) {
      const matching = (await this.received()).filter((m) => m.to.includes(address))
      const newest = matching.at(-1)
      if (newest !== undefined) return newest
      if (Date.now() >= deadline) {
        throw new Error(`no mail for ${address} within ${timeoutMs}ms`)
      }
      await new Promise((resolve) => setTimeout(resolve, 250))
    }
  },
}

/** The first http(s) link in a message body. */
export function firstLink(message: CapturedEmail): string {
  const found = message.text.match(/https?:\/\/\S+/)
  if (found === null) throw new Error(`no link in message "${message.subject}"`)
  return found[0]
}
