// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Forced-logout arrivals on /login.
 *
 * A server-side dead-session redirect (dashboard layout catching a 401
 * from the backend) reaches /login while the auth cookie is still
 * cryptographically valid — an RSC redirect cannot clear cookies. The
 * middleware's "authenticated user on /login → /dashboard" convenience
 * would bounce that request straight back into the dead session, looping
 * forever. These reasons mark /login arrivals that must render: the login
 * screen shows the notice and clears the stale client session itself.
 * Values match what the login screen and the api client's forced-logout
 * flow use.
 */
const FORCED_LOGOUT_REASONS = new Set(["idle_timeout", "session_expired"])

export function isForcedLogoutArrival(searchParams: URLSearchParams): boolean {
  return FORCED_LOGOUT_REASONS.has(searchParams.get("reason") ?? "")
}
