// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Serving the patient portal on a host of its own.
 *
 * A portal session is a bearer token in `localStorage`, keyed by practice
 * slug (`@/lib/portal-shell/session`). Storage is per origin, so while the
 * portal shares an origin with the clinician app, every script the clinician
 * app loads can read every portal session held in that browser, and a person
 * who is both a clinician and a client keeps both in one store. A deployment
 * that names one or more portal hosts in `PORTAL_HOSTS` gets the portal on an
 * origin of its own:
 *
 *   - on a portal host, `/{slug}/...` is rewritten to the portal route
 *     `/portal/{slug}/...`, `/portal/...` itself is still served (the links
 *     the shell renders use it), build assets, files and the few frontend
 *     API routes the portal page itself calls pass through, and everything
 *     else answers 404 — the clinician app is not reachable there at all;
 *   - on any other host, `/portal/...` is permanently redirected to the first
 *     portal host, so links already sent keep working.
 *
 * Unset or empty, nothing here changes a single response.
 *
 * Pure: the proxy (`frontend/proxy.ts`) hands in the request's host, path and
 * query, and acts on the decision. Everything that can be unit-tested lives
 * here.
 */

/** What the proxy should do with a request. */
export type PortalHostDecision =
  /** Not a portal-host concern — the request is handled exactly as it always was. */
  | { kind: "default" }
  /** On a portal host: let it through untouched (build assets, files, the portal's frontend API routes). */
  | { kind: "pass" }
  /** On a portal host: serve the portal route at `pathname` in place of the requested one. */
  | { kind: "rewrite"; pathname: string }
  /** On a portal host: the request is already a portal route; serve it as it is. */
  | { kind: "portal" }
  /** On a portal host: nothing to serve here. */
  | { kind: "not-found" }
  /** Off a portal host: the portal moved; send the visitor there. */
  | { kind: "redirect"; location: string }
  /** On a practice's own host: whether the host serves anything could not be found out. */
  | { kind: "unavailable" }

export interface PortalHostRequest {
  /** The `Host` header as the request carried it, port included when present. */
  host: string | null
  pathname: string
  /** `request.nextUrl.search`: empty, or starting with `?`. */
  search: string
  /** `request.nextUrl.protocol`: `"http:"` or `"https:"`. */
  protocol: string
}

const PORTAL_PREFIX = "/portal"

/**
 * First path segments the clinician app serves (`frontend/app`). On a portal
 * host each of these answers 404 rather than being read as a practice slug:
 * `/dashboard` there is a clinician reaching for the wrong door, not a
 * practice called "dashboard". A unit test keeps this list in step with the
 * route tree.
 *
 * Isolation does not depend on this list. A path that is not on it is read
 * as a slug and served by the portal route, never by a clinician page; the
 * list only decides whether the answer is a 404 or the portal's own
 * "no such practice" state.
 *
 * The backend never mints one of these as a practice slug: its
 * `_RESERVED_SLUGS` (backend/app/portal/practice_routes.py) carries the same
 * names, and a unit test here fails when one is missing there.
 */
export const CLINICIAN_ROUTE_SEGMENTS: ReadonlySet<string> = new Set([
  "auth",
  "book",
  "dashboard",
  "fbauth-proxy",
  "launch",
  "login",
  "mfa-enrollment",
  "mfa-step-up",
  "native-auth",
  "onboarding",
])

/**
 * The frontend's own API routes (`frontend/app/api`) a portal page calls.
 * Only these answer on a portal host; every other one — the clinician
 * sign-in routes under `/api/auth`, the `/api/login` / `/api/logout` cookie
 * endpoints, anything added later — answers 404 there. A unit test fails
 * when a route is added under `frontend/app/api` without a decision here.
 *
 * `/api/config` is how the page learns where the API is. Everything else the
 * portal calls goes to that API origin, not to this server.
 *
 * This concerns the frontend server only. The backend's `/api` routes are
 * not affected: a page dials them at the configured API origin, and where a
 * deployment shares one host between the two, the load balancer sends
 * backend `/api` paths to the backend before this proxy ever sees them.
 */
export const PORTAL_FRONTEND_API_ROUTES: ReadonlySet<string> = new Set(["/api/config"])

export const LOOPBACK_HOSTNAMES: ReadonlySet<string> = new Set(["localhost", "127.0.0.1", "[::1]"])

/**
 * The portal hosts a deployment names in `PORTAL_HOSTS`, normalized:
 * trimmed, lowercased, empties dropped. Order is kept — the first entry is
 * where the redirect off the clinician host goes.
 *
 * Read from `process.env` at request time rather than baked in at build, so
 * one image can serve the portal on its own host in one deployment and on
 * the shared host in another. (The proxy runs in the Node.js runtime, where
 * container env is visible per request.)
 */
export function portalHostsFromEnv(env: Record<string, string | undefined> = process.env): string[] {
  return parsePortalHosts(env.PORTAL_HOSTS)
}

export function parsePortalHosts(raw: string | undefined): string[] {
  return (raw ?? "")
    .split(",")
    .map((entry) => entry.trim().toLowerCase())
    .filter(Boolean)
}

/** `host` without its port; brackets kept on an IPv6 literal. */
export function hostnameOf(host: string): string {
  if (host.startsWith("[")) {
    const end = host.indexOf("]")
    return end === -1 ? host : host.slice(0, end + 1)
  }
  const colon = host.indexOf(":")
  return colon === -1 ? host : host.slice(0, colon)
}

/**
 * Whether the request's host is one of `portalHosts`. Case-insensitive. An
 * entry with a port matches only that host and port; an entry without one
 * matches its name on any port, so a proxy that forwards `name:443` still
 * matches `name`.
 */
export function isPortalHost(host: string | null, portalHosts: readonly string[]): boolean {
  if (!host) return false
  const requestHost = host.trim().toLowerCase()
  const requestHostname = hostnameOf(requestHost)
  return portalHosts.some((entry) =>
    hostnameOf(entry) === entry ? entry === requestHostname : entry === requestHost,
  )
}

/**
 * A path that names a file (`robots.txt`, `icon.png`, `.well-known/…`). Any
 * dot anywhere counts, the same test the proxy matcher applies before a
 * request ever gets here; a practice slug never contains one.
 */
export function hasFileExtension(pathname: string): boolean {
  return pathname.includes(".")
}

export function isUnder(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`)
}

/**
 * A first path segment that can be a practice slug. Not a Next internal or
 * a private segment (`_`), not a dotfile or dot-directory (`.`), not one of
 * the prefixes the portal host serves as themselves, and not a clinician
 * route (see {@link CLINICIAN_ROUTE_SEGMENTS}).
 */
function isSlugSegment(segment: string): boolean {
  if (!segment) return false
  if (segment.startsWith("_") || segment.startsWith(".")) return false
  if (segment === "portal" || segment === "api") return false
  return !CLINICIAN_ROUTE_SEGMENTS.has(segment)
}

/** What to do with a request that arrived on a portal host. */
function routeOnPortalHost(pathname: string): PortalHostDecision {
  // The Firebase auth helper serves clinician sign-in (and the clinician's
  // password-reset page behind `/__/auth/action`). The portal never signs
  // anyone in through it, so on the portal host it does not exist. Checked
  // first: `/__/firebase/init.json` would otherwise pass as a file.
  if (isUnder(pathname, "/__")) return { kind: "not-found" }
  if (isUnder(pathname, "/api")) {
    return PORTAL_FRONTEND_API_ROUTES.has(pathname) ? { kind: "pass" } : { kind: "not-found" }
  }
  if (isUnder(pathname, "/_next")) return { kind: "pass" }
  if (pathname === "/favicon.ico" || hasFileExtension(pathname)) return { kind: "pass" }
  if (isUnder(pathname, PORTAL_PREFIX)) return { kind: "portal" }

  // A practice's own host (one host, one practice, no slug in the path) never
  // reaches here: the proxy looks it up first, see `./practice-host`.
  const segment = pathname.split("/")[1] ?? ""
  if (!isSlugSegment(segment)) return { kind: "not-found" }
  return { kind: "rewrite", pathname: `${PORTAL_PREFIX}${pathname}` }
}

/**
 * Decide how a request is served given the deployment's portal hosts.
 *
 * With no portal hosts configured this always answers `default`, which the
 * proxy treats as "exactly what happened before this existed".
 */
export function routePortalHost(
  request: PortalHostRequest,
  portalHosts: readonly string[],
): PortalHostDecision {
  if (portalHosts.length === 0) return { kind: "default" }

  if (isPortalHost(request.host, portalHosts)) return routeOnPortalHost(request.pathname)

  if (!isUnder(request.pathname, PORTAL_PREFIX)) return { kind: "default" }

  const target = portalHosts[0]
  // A deployed portal host is https. A loopback one is a local stack, which
  // is plain http, so it keeps whatever the request came in on.
  const protocol = LOOPBACK_HOSTNAMES.has(hostnameOf(target)) ? request.protocol : "https:"
  const rest = request.pathname.slice(PORTAL_PREFIX.length) || "/"
  return { kind: "redirect", location: `${protocol}//${target}${rest}${request.search}` }
}
