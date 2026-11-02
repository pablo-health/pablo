// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Requests the app makes on a timer, as opposed to ones the person caused.
 *
 * The API's idle clock (the automatic sign-out after a quiet spell) is reset
 * by every request it receives. A page that polls every minute would keep an
 * untouched screen signed in forever, so polled requests are sent with
 * `X-Pablo-Background: 1`: the API still refuses them once the session has
 * gone idle, but does not count them as activity.
 *
 * A counter rather than a request option, so a poll can be marked without
 * threading a flag through every API function between the query and the
 * fetch. `apiClient` reads it synchronously on entry, which is why
 * `runAsBackground` only marks what its callback starts before it first
 * awaits (see `pollingOptions` in `@/lib/query/polling`).
 */

export const BACKGROUND_REQUEST_HEADER = "X-Pablo-Background"

let backgroundDepth = 0

/** Run `start`, marking every request it begins synchronously as background. */
export function runAsBackground<T>(start: () => T): T {
  backgroundDepth += 1
  try {
    return start()
  } finally {
    backgroundDepth -= 1
  }
}

/** True while inside `runAsBackground`. */
export function isBackgroundRequest(): boolean {
  return backgroundDepth > 0
}
