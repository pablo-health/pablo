// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What this server's backend calls on a practice host say about whose they
 * are (the headers are in `@/lib/api/clientForwarding`).
 *
 * Every visitor's request for a practice's website or portal host reaches the
 * backend from this server's one address, and the backend can count requests
 * per address (`GENERAL_IP_RATE_LIMIT_PER_MIN`). So a file fetch names the
 * visitor it is for, and a lookup, whose answer is kept and shared by every
 * visitor, says it is this server's own and names no one. Both carry the
 * forward secret, without which the backend ignores them. With no
 * FRONTEND_FORWARD_SECRET they carry nothing.
 */

import { FORWARD_AUTH_HEADER, buildForwardedClientHeaders, trustedProxyHops } from "@/lib/api/clientForwarding"

type Env = Record<string, string | undefined>

/** The headers on a call this server makes for itself, on behalf of no one visitor. */
export function serverForwardHeaders(env: Env = process.env): Record<string, string> {
  const secret = env.FRONTEND_FORWARD_SECRET?.trim()
  return secret ? { [FORWARD_AUTH_HEADER]: secret } : {}
}

/**
 * The headers on a call this server makes for the visitor whose request
 * `incoming` holds. When their address is not known, the call is this
 * server's own.
 */
export function visitorForwardHeaders(incoming: Pick<Headers, "get">, env: Env = process.env): Record<string, string> {
  const visitor = buildForwardedClientHeaders({
    secret: env.FRONTEND_FORWARD_SECRET,
    forwardedFor: incoming.get("x-forwarded-for"),
    userAgent: incoming.get("user-agent"),
    hops: trustedProxyHops(env.TRUSTED_PROXY_HOPS),
  })
  return Object.keys(visitor).length > 0 ? visitor : serverForwardHeaders(env)
}
