// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Asking the backend what a practice's own host serves, and remembering the
 * answer for a minute.
 *
 * `GET {API_URL}/api/portal/hosts/{host}` answers `{slug, primary_host}` for
 * an active portal host and 404 for every other. Both are kept for
 * `ttlMs`, at most `maxEntries` hosts at once with the oldest dropped first,
 * so a visitor sending made-up hostnames costs one backend call per name and
 * bounded memory. A failure to ask — the backend unreachable, slow, or
 * answering anything else — is not kept: the request answers 503 and the
 * next one asks again. Concurrent requests for the same host share one call.
 *
 * The backend keeps its answers for the same minute, so a host that starts
 * or stops working is noticed here within about two.
 *
 * A practice's website hosts are looked up the same way, against their own
 * route (`./practice-site-lookup`), through {@link createHostLookup}.
 */

import { type PracticeHost, type PracticeHostAnswer, practiceHostname } from "./practice-host"
import { parsePracticeHeader } from "./practice-header"
import { parsePracticeTheme } from "./practice-theme"

export interface PracticeHostLookupOptions {
  /** The backend's origin, as the server reaches it. Read per call. */
  apiUrl: () => string
  fetch?: typeof fetch
  /** Milliseconds; injectable so tests can move time. */
  now?: () => number
  ttlMs?: number
  /** How long a found answer is kept, when that differs from `ttlMs` (which then covers only a 404). */
  foundTtlMs?: number
  maxEntries?: number
  timeoutMs?: number
}

export interface HostLookupRoute<T> {
  /** The backend path that answers for `hostname`. */
  path: (hostname: string) => string
  /** A found answer from the response body, or `"unavailable"` when it makes no sense. */
  parse: (body: unknown) => T | "unavailable"
}

export type HostLookup<T> = (hostname: string) => Promise<T | null | "unavailable">
export type PracticeHostLookup = (hostname: string) => Promise<PracticeHostAnswer>

const TTL_MS = 60_000
const MAX_ENTRIES = 5_000
const TIMEOUT_MS = 3_000
const SLUG_FOUND_TTL_MS = 24 * 60 * 60_000

function parsePortalHost(body: unknown): PracticeHost | "unavailable" {
  if (typeof body !== "object" || body === null) return "unavailable"
  const { slug, primary_host: primaryHost, theme, site_host: siteHost } = body as Record<string, unknown>
  if (typeof slug !== "string" || !slug) return "unavailable"
  if (primaryHost !== null && typeof primaryHost !== "string") return "unavailable"
  // A theme or website host that does not parse is none: the portal is still
  // served, in its own look and with no link back. The website host becomes a
  // link, so only a plain hostname gets through. The header rides in the
  // theme and is read on its own, its paths made addresses on that host.
  const site = typeof siteHost === "string" ? practiceHostname(siteHost) : null
  const header = typeof theme === "object" && theme !== null ? (theme as Record<string, unknown>).header : null
  return {
    slug,
    primaryHost,
    theme: parsePracticeTheme(theme),
    siteHost: site,
    header: parsePracticeHeader(header, site),
  }
}

/** A cached lookup of `route` on the backend: found, nothing (404), or could not be asked. */
export function createHostLookup<T>(options: PracticeHostLookupOptions, route: HostLookupRoute<T>): HostLookup<T> {
  const fetchFn = options.fetch ?? fetch
  const now = options.now ?? Date.now
  const ttlMs = options.ttlMs ?? TTL_MS
  const foundTtlMs = options.foundTtlMs ?? ttlMs
  const maxEntries = options.maxEntries ?? MAX_ENTRIES
  const timeoutMs = options.timeoutMs ?? TIMEOUT_MS
  const kept = new Map<string, { until: number; answer: T | null }>()
  const inFlight = new Map<string, Promise<T | null | "unavailable">>()

  async function ask(hostname: string): Promise<T | null | "unavailable"> {
    const base = options.apiUrl().replace(/\/+$/, "")
    let response: Response
    try {
      response = await fetchFn(`${base}${route.path(hostname)}`, {
        headers: { Accept: "application/json" },
        cache: "no-store",
        signal: AbortSignal.timeout(timeoutMs),
      })
    } catch {
      return "unavailable"
    }
    if (response.status === 404) return null
    if (!response.ok) return "unavailable"
    try {
      return route.parse(await response.json())
    } catch {
      return "unavailable"
    }
  }

  function keep(hostname: string, answer: T | null): void {
    kept.delete(hostname)
    kept.set(hostname, { until: now() + (answer === null ? ttlMs : foundTtlMs), answer })
    while (kept.size > maxEntries) {
      const oldest = kept.keys().next().value
      if (oldest === undefined) break
      kept.delete(oldest)
    }
  }

  return async (hostname) => {
    const entry = kept.get(hostname)
    if (entry && entry.until > now()) return entry.answer

    const pending = inFlight.get(hostname)
    if (pending) return pending

    const call = ask(hostname).then((answer) => {
      if (answer !== "unavailable") keep(hostname, answer)
      return answer
    })
    inFlight.set(hostname, call)
    try {
      return await call
    } finally {
      inFlight.delete(hostname)
    }
  }
}

export function createPracticeHostLookup(options: PracticeHostLookupOptions): PracticeHostLookup {
  return createHostLookup(options, {
    path: (hostname) => `/api/portal/hosts/${encodeURIComponent(hostname)}`,
    parse: parsePortalHost,
  })
}

/** The backend's origin as this server reaches it: the server-side `API_URL`. */
export function serverApiUrl(): string {
  return process.env.API_URL || "http://localhost:8000"
}

/** The process-wide lookup the proxy uses, against the server-side `API_URL`. */
export const lookupPracticeHost: PracticeHostLookup = createPracticeHostLookup({ apiUrl: serverApiUrl })

/**
 * Whether a practice holds `slug`: `true`, `null` (the backend's 404), or
 * `"unavailable"`.
 *
 * `GET {API_URL}/api/portal/practices/{slug}` is the call the portal shell
 * makes first in the browser. The proxy asks it as well, so that an address
 * naming no practice is served with a 404 status and the same card
 * (`./unknown-practice`) instead of a 200.
 *
 * A practice that has a portal is kept for a day. Being wrong about one is
 * cheap: a portal turned off or a slug given up within the day still gets the
 * page, which asks the same route and shows the practice missing itself. A
 * slug no practice holds is kept only a minute, so a portal that has just
 * been set up is not refused for a day.
 *
 * The route is rate-limited by address and every call from here shares this
 * server's, so under a flood of made-up slugs the answer turns
 * `"unavailable"` and the proxy serves the page as it did before.
 */
export function createPortalSlugLookup(options: PracticeHostLookupOptions): HostLookup<true> {
  return createHostLookup({ foundTtlMs: SLUG_FOUND_TTL_MS, ...options }, {
    path: (slug) => `/api/portal/practices/${encodeURIComponent(slug)}`,
    parse: () => true,
  })
}

/** The process-wide slug lookup the proxy uses. */
export const lookupPortalSlug: HostLookup<true> = createPortalSlugLookup({ apiUrl: serverApiUrl })
