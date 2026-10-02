// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Serving a practice's website on the practice's own website hosts.
 *
 * A practice can publish a static website (Settings > Website) and serve it
 * from website hosts of its own (Settings > Domains). A request for such a
 * host reaches this server like any practice host; the proxy looks it up as a
 * portal host first, and when it is none, as a website host:
 *
 *   - a working website host of a practice that has published serves the
 *     live version: every path is the website's, `/` and `/folder/` mean the
 *     folder's `index.html`, and a missing file is the website's `404.html`
 *     or a plain 404 (the backend decides which file, see
 *     `backend/app/sites/paths.py`);
 *   - any other website host of the practice permanently redirects to the
 *     same path and query on its primary, as a portal alias does;
 *   - on the practice's hosted website address (a deployment's hosted domain,
 *     `backend/app/portal/hosted.py`), `/portal` and everything under it
 *     permanently redirects to the practice's portal host. The portal is never
 *     served on a website's origin: the website runs the practice's own
 *     scripts, and a portal session lives in its origin's storage;
 *   - a host that is not working, has nothing published, or is nobody's is the
 *     same plain 404 as an unknown host;
 *   - only GET and HEAD: a website is static.
 *
 * The page never reaches the app. The proxy fetches the file from the backend
 * and answers with it and the headers below: no app shell, no app script, no
 * cookie, and nothing from the app's own frontend routes (`/api/login` and
 * the rest are website paths here, like any other). JavaScript the practice
 * wrote runs, on the practice's own origin.
 *
 * Pure: the lookup and the fetch are `./practice-site-lookup`.
 */

import type { PracticeHostRequest } from "./practice-host"
import { isUnder } from "./routing"

const PORTAL_PREFIX = "/portal"

/** What the backend says a website host serves. */
export interface SiteHost {
  /** The practice's working primary website host, if it has one. */
  primaryHost: string | null
  /** On a hosted website address only: the host the practice's portal is on. */
  portalHost: string | null
}

export type SiteHostAnswer = SiteHost | null | "unavailable"

export type SiteHostDecision =
  | { kind: "serve"; pathname: string }
  | { kind: "redirect"; location: string }
  | { kind: "not-found" }
  | { kind: "unavailable" }
  | { kind: "method-not-allowed" }

export const SITE_METHODS = ["GET", "HEAD"]

/**
 * The headers every website answer carries. The policy suits a static site
 * whose own scripts run: it may not be framed, and a page cannot repoint its
 * relative links (`base-uri`) or load plugins.
 */
export const SITE_SECURITY_HEADERS: Readonly<Record<string, string>> = {
  "Content-Security-Policy": "base-uri 'self'; object-src 'none'; frame-ancestors 'none'",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "X-Frame-Options": "DENY",
}

const HTML_MAX_AGE = 60
const ASSET_MAX_AGE = 3600

/**
 * How long a browser may keep an answer. A file's address carries no version,
 * so nothing is kept for long: a page a minute, other files an hour, and the
 * version's ETag lets a browser check cheaply after that.
 */
export function siteCacheControl(status: number, contentType: string | null): string {
  const isHtml = (contentType ?? "").startsWith("text/html")
  const maxAge = status === 200 && !isHtml ? ASSET_MAX_AGE : HTML_MAX_AGE
  return `public, max-age=${maxAge}`
}

/** Decide how a request on a possible website host is served, given the lookup's answer. */
export function routeSiteHost(request: PracticeHostRequest & { method: string }, found: SiteHostAnswer): SiteHostDecision {
  if (found === "unavailable") return { kind: "unavailable" }
  if (found === null) return { kind: "not-found" }
  if (!SITE_METHODS.includes(request.method)) return { kind: "method-not-allowed" }
  if (found.portalHost !== null && isUnder(request.pathname, PORTAL_PREFIX)) {
    const rest = request.pathname.slice(PORTAL_PREFIX.length) || "/"
    return { kind: "redirect", location: `https://${found.portalHost}${rest}${request.search}` }
  }
  if (found.primaryHost !== null && found.primaryHost !== request.hostname) {
    return { kind: "redirect", location: `https://${found.primaryHost}${request.pathname}${request.search}` }
  }
  return { kind: "serve", pathname: request.pathname }
}
