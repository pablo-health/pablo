// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The end user's IP and user agent, passed to the backend for audit records.
 *
 * When this app renders a page on its server, the server calls the backend
 * itself, so the backend would otherwise record the server's address and
 * agent instead of the user's. With FRONTEND_FORWARD_SECRET set (the backend
 * holds the same value), the server's backend calls carry the user's IP and
 * agent in dedicated headers, plus the secret that proves they come from this
 * server. The backend ignores the headers without it.
 *
 * This module is safe to load in the browser: it holds no secret and reads no
 * request. The server-only half that does is `clientForwarding.server.ts`,
 * registered at startup by `instrumentation.ts`.
 */

export const FORWARDED_CLIENT_IP_HEADER = "X-Pablo-Client-IP"
export const FORWARDED_CLIENT_UA_HEADER = "X-Pablo-Client-UA"
export const FORWARD_AUTH_HEADER = "X-Pablo-Frontend-Auth"

/**
 * The client IP from an X-Forwarded-For value, read the way the backend reads
 * its own: `hops` entries from the right (the end the trusted proxies append
 * to), never the leftmost entry, which the client can set to anything.
 */
export function clientIpFromForwardedFor(
  forwardedFor: string | null | undefined,
  hops: number,
): string | null {
  const parts = (forwardedFor ?? "")
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean)
  if (parts.length === 0) return null
  const trusted = Math.min(Math.max(1, Math.floor(hops) || 1), parts.length)
  return parts[parts.length - trusted]
}

/**
 * The trusted proxy hops in front of this app: `TRUSTED_PROXY_HOPS`, as the
 * backend reads it, defaulting to 1.
 */
export function trustedProxyHops(raw: string | undefined): number {
  const hops = Number.parseInt(raw ?? "", 10)
  return Number.isFinite(hops) && hops >= 1 ? hops : 1
}

export interface ForwardedClientInput {
  secret: string | undefined
  forwardedFor: string | null | undefined
  userAgent: string | null | undefined
  hops: number
}

/**
 * The headers that pass the end user's IP and agent to the backend, or none
 * when no secret is configured or the user's IP is not known.
 */
export function buildForwardedClientHeaders({
  secret,
  forwardedFor,
  userAgent,
  hops,
}: ForwardedClientInput): Record<string, string> {
  const key = secret?.trim()
  if (!key) return {}
  const ip = clientIpFromForwardedFor(forwardedFor, hops)
  if (!ip) return {}
  const headers: Record<string, string> = {
    [FORWARD_AUTH_HEADER]: key,
    [FORWARDED_CLIENT_IP_HEADER]: ip,
  }
  if (userAgent) headers[FORWARDED_CLIENT_UA_HEADER] = userAgent
  return headers
}

type ForwardedClientHeadersProvider = () => Promise<Record<string, string>>

// Kept on globalThis, not in a module variable: the server registers it from
// `instrumentation.ts`, which Next bundles separately from the pages that make
// the calls, so the two would not share a module instance.
const PROVIDER_KEY = Symbol.for("pablo.forwardedClientHeaders")

type ProviderHost = { [PROVIDER_KEY]?: ForwardedClientHeadersProvider }

export function registerForwardedClientHeaders(
  provider: ForwardedClientHeadersProvider | undefined,
): void {
  ;(globalThis as ProviderHost)[PROVIDER_KEY] = provider
}

/**
 * The forwarding headers for the request being rendered on the server, or
 * none (in the browser, when nothing is registered, or if reading the request
 * fails — a missing audit detail must never fail the page).
 */
export async function forwardedClientHeaders(): Promise<Record<string, string>> {
  const provider = (globalThis as ProviderHost)[PROVIDER_KEY]
  if (!provider) return {}
  try {
    return await provider()
  } catch {
    return {}
  }
}
