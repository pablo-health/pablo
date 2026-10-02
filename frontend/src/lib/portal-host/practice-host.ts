// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Serving a practice's portal on the practice's own host.
 *
 * A practice can add hosts of its own (Settings > Domains). Once one is
 * working, requests for it reach this same server, and it serves that
 * practice's portal at the root — `https://portal.example.com/` is the
 * practice's Home, `/messaging` its Messages — with nothing else reachable
 * there:
 *
 *   - an active host that is the practice's primary (or of a practice with
 *     no working primary) serves the portal: `/{rest}` is rewritten to
 *     `/portal/{slug}/{rest}`, so the address bar never changes;
 *   - any other active host of the practice permanently redirects to the
 *     same path and query on the primary;
 *   - `/portal/{slug}/...` and `/{slug}/...` for the practice's own slug
 *     permanently redirect to the slug-less path, so an old link still lands;
 *     `/portal/...` for any other practice answers 404 — a practice's host
 *     only ever serves that practice;
 *   - a host that is not active, not a portal host, or nobody's answers 404:
 *     never another practice's portal, and never the clinician app.
 *
 * Build assets, files and the frontend API routes the portal page calls pass
 * through, the same list a portal host lets through (`./routing`).
 *
 * Which hosts are looked up at all is decided by `APP_HOSTS`: the names this
 * deployment serves the clinician app on. A host that is one of those, one of
 * `PORTAL_HOSTS`, a loopback name, a name with no dot or an IP address is
 * handled exactly as it was before practice hosts existed. Any other host is looked up. Unset or
 * empty, nothing is looked up and nothing here changes a single response.
 *
 * Pure: the lookup is `./practice-host-lookup`, and the proxy
 * (`frontend/proxy.ts`) hands in what it found.
 */

import type { PracticeTheme } from "./practice-theme"
import {
  CLINICIAN_ROUTE_SEGMENTS,
  PORTAL_FRONTEND_API_ROUTES,
  type PortalHostDecision,
  hasFileExtension,
  hostnameOf,
  isPortalHost,
  isUnder,
  parsePortalHosts,
} from "./routing"

/** What the backend says a practice's host serves. */
export interface PracticeHost {
  /** The practice's portal address, the `{slug}` in `/portal/{slug}`. */
  slug: string
  /** The practice's working primary portal host, if it has one. */
  primaryHost: string | null
  /** The theme the portal wears on the practice's hosts (`./practice-theme`), if any. */
  theme: PracticeTheme | null
  /** The host the practice's live website is served at, which the portal links back to. */
  siteHost: string | null
}

/** The lookup's answer: found, serves nothing, or could not be asked. */
export type PracticeHostAnswer = PracticeHost | null | "unavailable"

/** How the proxy should treat a request's host. */
export type HostClass =
  /** One of this deployment's own hosts, or lookups are off: as before. */
  | { kind: "canonical" }
  /** Possibly a practice's own host: look `hostname` up. */
  | { kind: "practice"; hostname: string }
  /** A host no practice could hold and this deployment does not serve. */
  | { kind: "unknown" }

const PORTAL_PREFIX = "/portal"
const MAX_HOST_LENGTH = 253
const LABEL = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/
const IPV4 = /^\d{1,3}(?:\.\d{1,3}){3}$/

/** `APP_HOSTS`, normalized like `PORTAL_HOSTS`. Read per request, like it. */
export function appHostsFromEnv(env: Record<string, string | undefined> = process.env): string[] {
  return parsePortalHosts(env.APP_HOSTS)
}

/**
 * `host` as a stored hostname would read — lowercased, port and trailing dot
 * removed — or `null` when no practice could hold it.
 */
export function practiceHostname(host: string): string | null {
  const name = hostnameOf(host.trim().toLowerCase()).replace(/\.$/, "")
  if (!name || name.length > MAX_HOST_LENGTH || name.startsWith("[")) return null
  const labels = name.split(".")
  if (labels.length < 2 || !labels.every((label) => LABEL.test(label))) return null
  if (IPV4.test(name)) return null
  return name
}

/**
 * Whether the request's host is one of `appHosts`. An entry matches like a
 * `PORTAL_HOSTS` entry, and `*.example.com` matches any name under it (not
 * `example.com` itself), on any port.
 */
export function isAppHost(host: string, appHosts: readonly string[]): boolean {
  const hostname = hostnameOf(host.trim().toLowerCase()).replace(/\.$/, "")
  const exact = appHosts.filter((entry) => !entry.startsWith("*."))
  if (isPortalHost(host, exact)) return true
  return appHosts.some(
    (entry) => entry.startsWith("*.") && hostname.endsWith(entry.slice(1)) && hostname.length > entry.length - 1,
  )
}

/**
 * An address, a loopback name, or a name with no dot (a container's service
 * name, say): reached from inside, never a host a practice could hold.
 */
function isInternal(host: string): boolean {
  const hostname = hostnameOf(host.trim().toLowerCase()).replace(/\.$/, "")
  return (
    hostname.startsWith("[") ||
    IPV4.test(hostname) ||
    !hostname.includes(".") ||
    hostname.endsWith(".localhost")
  )
}

/** Decide whether a request's host is looked up as a practice's own host. */
export function classifyHost(
  host: string | null,
  appHosts: readonly string[],
  portalHosts: readonly string[],
): HostClass {
  if (appHosts.length === 0 || !host) return { kind: "canonical" }
  if (isInternal(host)) return { kind: "canonical" }
  if (isAppHost(host, appHosts) || isPortalHost(host, portalHosts)) return { kind: "canonical" }
  const hostname = practiceHostname(host)
  return hostname ? { kind: "practice", hostname } : { kind: "unknown" }
}

export interface PracticeHostRequest {
  /** The `Host` header as the request carried it. */
  host: string
  /** The same host, normalized (`practiceHostname`). */
  hostname: string
  pathname: string
  /** `request.nextUrl.search`: empty, or starting with `?`. */
  search: string
  /** `request.nextUrl.protocol`: `"http:"` or `"https:"`. */
  protocol: string
}

/**
 * The path a request means on the practice's host with the practice's own
 * slug taken out, `null` when it names another practice, or `undefined` when
 * it carries no slug at all.
 */
function withoutOwnSlug(pathname: string, slug: string): string | null | undefined {
  const segments = pathname.split("/")
  if (isUnder(pathname, PORTAL_PREFIX)) {
    if (segments[2] !== slug) return null
    return `/${segments.slice(3).join("/")}`
  }
  if (segments[1] === slug) return `/${segments.slice(2).join("/")}`
  return undefined
}

/** Decide how a request on a practice's own host is served, given the lookup's answer. */
export function routePracticeHost(request: PracticeHostRequest, found: PracticeHostAnswer): PortalHostDecision {
  if (found === "unavailable") return { kind: "unavailable" }
  if (found === null) return { kind: "not-found" }

  const { pathname, search } = request
  // Same order and reasons as a portal host (`./routing`).
  if (isUnder(pathname, "/__")) return { kind: "not-found" }
  if (isUnder(pathname, "/api")) {
    return PORTAL_FRONTEND_API_ROUTES.has(pathname) ? { kind: "pass" } : { kind: "not-found" }
  }
  if (isUnder(pathname, "/_next")) return { kind: "pass" }
  if (pathname === "/favicon.ico" || hasFileExtension(pathname)) return { kind: "pass" }

  const stripped = withoutOwnSlug(pathname, found.slug)
  if (stripped === null) return { kind: "not-found" }
  if (stripped === undefined) {
    // Not a slug: a clinician page, or a private or dot segment, is nothing
    // here — the portal route would only answer it with its own not-found.
    const segment = pathname.split("/")[1] ?? ""
    if (segment.startsWith("_") || segment.startsWith(".") || CLINICIAN_ROUTE_SEGMENTS.has(segment)) {
      return { kind: "not-found" }
    }
  }

  const elsewhere = found.primaryHost !== null && found.primaryHost !== request.hostname
  if (stripped !== undefined || elsewhere) {
    // A practice's host is https wherever it is served for real; a request
    // that stays on this host keeps whatever it came in on.
    const origin = elsewhere ? `https://${found.primaryHost}` : `${request.protocol}//${request.host}`
    return { kind: "redirect", location: `${origin}${stripped ?? pathname}${search}` }
  }

  const rest = pathname === "/" ? "" : pathname
  return { kind: "rewrite", pathname: `${PORTAL_PREFIX}/${encodeURIComponent(found.slug)}${rest}` }
}
