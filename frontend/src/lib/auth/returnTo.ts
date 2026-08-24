// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Where to send a user once they finish signing in.
 *
 * A session that dies on the idle clock takes the user off whatever page they
 * were on, mid-task. Sending every sign-in to the dashboard means the
 * interruption also costs them their place — step away from a half-written
 * note and you come back somewhere else entirely. The forced-logout path
 * records the page it interrupted in a `returnTo` query parameter; this reads
 * it back.
 *
 * The value arrives on the query string, where anything can put it, so it is
 * validated rather than trusted:
 *
 *   - it must be same-origin, i.e. a bare path. An absolute URL would send the
 *     user to another site straight out of our login screen.
 *   - the protocol-relative `//host/path` form is rejected explicitly. It
 *     passes a naive `startsWith("/")` check and the browser still resolves it
 *     as an absolute URL to another origin — the subtle half of an open
 *     redirect.
 *   - `/login` is rejected so a boot that fires on the login screen can't
 *     round-trip into itself and strand the user there.
 */
export const DEFAULT_POST_LOGIN_DESTINATION = "/dashboard"

export function safeReturnTo(value: string | null | undefined): string {
  if (!value) return DEFAULT_POST_LOGIN_DESTINATION
  if (!value.startsWith("/") || value.startsWith("//")) {
    return DEFAULT_POST_LOGIN_DESTINATION
  }
  if (value === "/login" || value.startsWith("/login?") || value.startsWith("/login/")) {
    return DEFAULT_POST_LOGIN_DESTINATION
  }
  return value
}
