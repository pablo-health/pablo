// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Which theme a portal page wears: the practice's own, on a host that is that
 * practice's working portal host, and none anywhere else — the app's own
 * hosts, the shared portal host, and a lookup that could not be made all
 * serve the portal in its own look.
 *
 * The answer comes from the same cached lookup the proxy has just made for
 * the request (`./practice-host-lookup`), so it costs no second backend call.
 */

import { appHostsFromEnv, classifyHost } from "./practice-host"
import { type PracticeHostLookup, lookupPracticeHost } from "./practice-host-lookup"
import type { PracticeTheme } from "./practice-theme"
import { portalHostsFromEnv } from "./routing"

/** The theme for the portal of `slug` served to a request with `host`, or `null`. */
export async function practiceThemeFor(
  host: string | null,
  slug: string,
  lookup: PracticeHostLookup = lookupPracticeHost,
  env: Record<string, string | undefined> = process.env,
): Promise<PracticeTheme | null> {
  const hostClass = classifyHost(host, appHostsFromEnv(env), portalHostsFromEnv(env))
  if (hostClass.kind !== "practice") return null
  const found = await lookup(hostClass.hostname)
  if (found === null || found === "unavailable" || found.slug !== slug) return null
  return found.theme
}
