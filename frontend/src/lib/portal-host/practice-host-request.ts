// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Whether a portal page is being served on the practice's own host, and if so
 * what the backend said about it: the theme the portal wears there, among
 * other things. `null` everywhere else — the app's own hosts, the shared
 * portal host, a lookup that could not be made — where the portal is served
 * in its own look, as it always was.
 *
 * The answer comes from the same cached lookup the proxy has just made for
 * the request (`./practice-host-lookup`), so it costs no second backend call.
 */

import { type PracticeHost, appHostsFromEnv, classifyHost } from "./practice-host"
import { type PracticeHostLookup, lookupPracticeHost } from "./practice-host-lookup"
import { portalHostsFromEnv } from "./routing"

/** What the practice's own host serves, for the portal of `slug` reached with `host`; `null` off one. */
export async function portalPracticeHost(
  host: string | null,
  slug: string,
  lookup: PracticeHostLookup = lookupPracticeHost,
  env: Record<string, string | undefined> = process.env,
): Promise<PracticeHost | null> {
  const hostClass = classifyHost(host, appHostsFromEnv(env), portalHostsFromEnv(env))
  if (hostClass.kind !== "practice") return null
  const found = await lookup(hostClass.hostname)
  if (found === null || found === "unavailable" || found.slug !== slug) return null
  return found
}
