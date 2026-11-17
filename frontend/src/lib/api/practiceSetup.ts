// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Holding a request while the signed-in practice is being set up.
 *
 * The API answers 503 with one of these codes when the practice's schema is
 * not in the database yet: `TENANT_PROVISIONING_PENDING` when it has not been
 * built (or was archived and is being restored), `TENANT_PROVISIONING_IN_PROGRESS`
 * while it is being built. Both carry a `Retry-After`, and a deployment builds
 * or restores the schema in response to that same request, so the right move
 * is to wait and ask again rather than fail the call.
 *
 * `waitOutPracticeSetup` does that for `apiClient`, for up to
 * `PRACTICE_SETUP_BUDGET_MS`, and while any request is waiting the store below
 * says so; `PracticeSetupScreen` covers the page with a setup state until then.
 * The state leaves when the waiting request gets an answer, never on a timer:
 * nothing here claims the practice is ready, only the next successful response
 * does. Past the budget the last 503 is handed back and the caller shows its
 * ordinary error.
 */

export const PRACTICE_SETUP_CODES: ReadonlySet<string> = new Set([
  "TENANT_PROVISIONING_PENDING",
  "TENANT_PROVISIONING_IN_PROGRESS",
])

/** How long one request keeps asking before its last 503 becomes an error. */
export const PRACTICE_SETUP_BUDGET_MS = 3 * 60 * 1000

const DEFAULT_RETRY_AFTER_MS = 1000
const MAX_RETRY_AFTER_MS = 30 * 1000

let waiting = 0
const listeners = new Set<() => void>()

function notify(): void {
  for (const listener of listeners) listener()
}

/** `useSyncExternalStore` subscription for the setup state. */
export function subscribePracticeSetup(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** Whether any request is waiting for the practice to be set up. */
export function isPracticeSetupInProgress(): boolean {
  return waiting > 0
}

/** The server never holds a request; it has no setup state to show. */
export function isPracticeSetupInProgressOnServer(): boolean {
  return false
}

/**
 * `Retry-After` in milliseconds: whole seconds as the API sends them, at
 * least one second and at most thirty, one second when absent or unreadable.
 */
export function retryAfterMs(response: Response): number {
  const raw = response.headers.get("retry-after")
  const seconds = raw === null ? Number.NaN : Number(raw)
  if (!Number.isFinite(seconds)) return DEFAULT_RETRY_AFTER_MS
  return Math.min(Math.max(seconds * 1000, DEFAULT_RETRY_AFTER_MS), MAX_RETRY_AFTER_MS)
}

export interface PracticeSetupDeps {
  /** The response's error code, read without consuming its body. */
  codeOf: (response: Response) => Promise<string | null>
  /** Ask again. */
  refetch: () => Promise<Response>
  now?: () => number
  sleep?: (ms: number) => Promise<void>
}

async function isSetupAnswer(
  response: Response,
  codeOf: PracticeSetupDeps["codeOf"],
): Promise<boolean> {
  if (response.status !== 503) return false
  return PRACTICE_SETUP_CODES.has((await codeOf(response)) ?? "")
}

const realSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

/**
 * Return `response` untouched unless it is a setup 503; otherwise wait out
 * each `Retry-After` and ask again until an answer that is not a setup 503
 * arrives, or the budget would be overrun, and return that last response.
 */
export async function waitOutPracticeSetup(
  response: Response,
  { codeOf, refetch, now = Date.now, sleep = realSleep }: PracticeSetupDeps,
): Promise<Response> {
  if (!(await isSetupAnswer(response, codeOf))) return response

  const deadline = now() + PRACTICE_SETUP_BUDGET_MS
  let current = response
  waiting += 1
  notify()
  try {
    for (;;) {
      const wait = retryAfterMs(current)
      if (now() + wait > deadline) return current
      await sleep(wait)
      current = await refetch()
      if (!(await isSetupAnswer(current, codeOf))) return current
    }
  } finally {
    waiting -= 1
    notify()
  }
}

/** Test seam: forget every waiting request and listener. */
export function resetPracticeSetupForTests(): void {
  waiting = 0
  listeners.clear()
}
