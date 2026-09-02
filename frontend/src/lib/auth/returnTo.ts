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

/**
 * Where the email-action page sends the user once the action succeeds.
 *
 * Firebase's action emails carry a `continueUrl` that the app itself set when
 * it asked for the email (an absolute URL on our own origin, such as
 * `https://app.example/mfa-enrollment`). But the page is reachable straight
 * from a link, so the parameter is attacker-writable: a crafted reset or
 * verification link would otherwise put a Pablo-branded "Continue" button in
 * front of a login page on someone else's domain, right after the user has
 * proven they own the account.
 *
 * Accepted: a bare same-origin path, or an absolute http(s) URL whose origin
 * matches `origin`. Either way only the path, query and hash are returned, so
 * the link can never leave the site. Everything else, including the
 * protocol-relative `//host` form and non-http schemes, falls back.
 */
export function safeContinuePath(
  value: string | null | undefined,
  origin: string,
  fallback = "/login",
): string {
  if (!value) return fallback
  if (value.startsWith("/")) {
    return value.startsWith("//") ? fallback : value
  }
  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    return fallback
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return fallback
  if (parsed.origin !== origin) return fallback
  return `${parsed.pathname}${parsed.search}${parsed.hash}`
}
