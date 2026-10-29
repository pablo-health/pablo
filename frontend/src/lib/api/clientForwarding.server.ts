// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Server half of `clientForwarding.ts`: reads the incoming request and the
 * forwarding secret. Imported only by `instrumentation.ts` in the Node.js
 * runtime, never by code that can reach the browser bundle.
 *
 * FRONTEND_FORWARD_SECRET is a server-only variable (no NEXT_PUBLIC_ prefix),
 * so Next never inlines it into client code.
 */

import { headers } from "next/headers"
import { buildForwardedClientHeaders, trustedProxyHops } from "./clientForwarding"

export async function forwardedClientHeadersForRequest(): Promise<Record<string, string>> {
  const secret = process.env.FRONTEND_FORWARD_SECRET
  if (!secret?.trim()) return {}
  const incoming = await headers()
  return buildForwardedClientHeaders({
    secret,
    forwardedFor: incoming.get("x-forwarded-for"),
    userAgent: incoming.get("user-agent"),
    hops: trustedProxyHops(process.env.TRUSTED_PROXY_HOPS),
  })
}
