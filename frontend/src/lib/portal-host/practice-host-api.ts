// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Where a portal page on a practice's own host calls the API: its own origin.
 *
 * A practice's host reaches the same frontend and backend as the app's host,
 * split by path the same way — the frontend's own API routes to the frontend,
 * every other `/api` path to the backend. So a page there calls the API on
 * the origin it was loaded from, which needs no CORS entry for every host a
 * practice adds, and keeps the portal's session in that origin's storage
 * alone.
 *
 * Only for a host the lookup says is a practice's working portal host. Every
 * other host — this deployment's own, or one that serves nothing, which the
 * proxy answers 404 before `/api/config` is ever reached — keeps the
 * configured API address.
 */

import { appHostsFromEnv, classifyHost } from "./practice-host"
import { type PracticeHostLookup, lookupPracticeHost } from "./practice-host-lookup"
import { portalHostsFromEnv } from "./routing"

export interface ApiOriginRequest {
  headers: Headers
  /** `request.nextUrl.protocol`: what this server was reached over. */
  protocol: string
}

/**
 * The scheme the browser used. Behind a load balancer this server is reached
 * over plain http whatever the visitor used, so the balancer's
 * `X-Forwarded-Proto` says which; without one, the request's own.
 */
function browserScheme(request: ApiOriginRequest): string {
  const forwarded = request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim().toLowerCase()
  if (forwarded === "https" || forwarded === "http") return `${forwarded}:`
  return request.protocol
}

/** The request's own origin when its host is a practice's working portal host, else `null`. */
export async function practiceHostApiOrigin(
  request: ApiOriginRequest,
  lookup: PracticeHostLookup = lookupPracticeHost,
  env: Record<string, string | undefined> = process.env,
): Promise<string | null> {
  const host = request.headers.get("host")
  const hostClass = classifyHost(host, appHostsFromEnv(env), portalHostsFromEnv(env))
  if (hostClass.kind !== "practice" || !host) return null
  const found = await lookup(hostClass.hostname)
  if (found === null || found === "unavailable") return null
  return `${browserScheme(request)}//${host.trim().toLowerCase()}`
}
