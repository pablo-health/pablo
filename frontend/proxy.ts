import { NextRequest, NextResponse } from "next/server"
import { authProviderMiddleware } from "@/lib/auth/middleware"
import { appHostsFromEnv, classifyHost, routePracticeHost } from "@/lib/portal-host/practice-host"
import { browserScheme } from "@/lib/portal-host/practice-host-api"
import { lookupPracticeHost } from "@/lib/portal-host/practice-host-lookup"
import { SITE_METHODS, routeSiteHost } from "@/lib/portal-host/practice-site"
import { fetchSiteFile, lookupSiteHost } from "@/lib/portal-host/practice-site-lookup"
import {
  type PortalHostDecision,
  hasFileExtension,
  isUnder,
  portalHostsFromEnv,
  routePortalHost,
} from "@/lib/portal-host/routing"

const BUILD_ASSET_PREFIX = "/_next/static"
const BUILD_ASSET_METHODS = ["GET", "HEAD"]
const FIREBASE_HELPER_PREFIX = "/__/"

// Route protection is delegated to the active auth provider
// (NEXT_PUBLIC_AUTH_PROVIDER, default "firebase"). See
// src/lib/auth/middleware.ts and the provider's middleware impl.
//
// Build assets under /_next/static are immutable files, so reads are the
// only thing that makes sense there. Anything else gets a 405 here rather
// than falling through to the server-action handler, which answers an
// unrecognized action id with a 500. Reads are passed through untouched —
// routing them through the auth provider would send asset loads to the
// login redirect.
//
// A deployment can serve the patient portal on a host of its own
// (PORTAL_HOSTS; see src/lib/portal-host/routing.ts). On that host the
// clinician app is not served at all. Unset, every request below takes the
// "default" branch, which is the behaviour from before portal hosts existed.
//
// A practice can also serve its portal from a host of its own (APP_HOSTS
// turns this on; see src/lib/portal-host/practice-host.ts). A host that is
// not one of this deployment's is looked up, and serves that practice's
// portal at its root, that practice's website (src/lib/portal-host/practice-site.ts),
// or nothing at all.
//
// A website has files of its own at any path, so file paths are matched here
// too; on every host but a website host they pass straight through, exactly
// as when the matcher left them out. For the same reason Next's own
// trailing-slash redirect is off (next.config.ts) and done here instead, on
// every host but a website host, where `/folder/` is a folder.
export default async function proxy(request: NextRequest) {
  const { pathname, search, protocol } = request.nextUrl
  if (pathname.startsWith(BUILD_ASSET_PREFIX)) {
    return BUILD_ASSET_METHODS.includes(request.method)
      ? NextResponse.next()
      : new NextResponse(null, { status: 405, headers: { Allow: BUILD_ASSET_METHODS.join(", ") } })
  }

  const host = request.headers.get("host")
  const portalHosts = portalHostsFromEnv()
  const hostClass = classifyHost(host, appHostsFromEnv(), portalHosts)
  const isFile = hasFileExtension(pathname) && !isUnder(pathname, "/__")
  if (hostClass.kind !== "practice" && isFile) return NextResponse.next()

  let decision: PortalHostDecision
  if (hostClass.kind === "practice" && host) {
    const found = await lookupPracticeHost(hostClass.hostname)
    if (found === null) return serveSiteHost(request, host, hostClass.hostname)
    if (isFile && found !== "unavailable") return NextResponse.next()
    decision = routePracticeHost({ host, hostname: hostClass.hostname, pathname, search, protocol }, found)
  } else if (hostClass.kind === "unknown") {
    decision = { kind: "not-found" }
  } else {
    decision = routePortalHost({ host, pathname, search, protocol }, portalHosts)
  }
  const slashless = withoutTrailingSlash(request)
  if (slashless && decision.kind !== "not-found") return slashless
  switch (decision.kind) {
    case "pass":
      return NextResponse.next()
    case "not-found":
      return new NextResponse("Not Found", {
        status: 404,
        headers: { "Content-Type": "text/plain; charset=utf-8", "X-Content-Type-Options": "nosniff" },
      })
    case "unavailable":
      return new NextResponse("Service Unavailable", {
        status: 503,
        headers: {
          "Content-Type": "text/plain; charset=utf-8",
          "X-Content-Type-Options": "nosniff",
          "Retry-After": "5",
        },
      })
    case "redirect":
      return NextResponse.redirect(decision.location, 301)
    case "rewrite":
      return servePortalAt(request, decision.pathname)
    case "portal":
      return authProviderMiddleware(request)
    case "default":
      // The Firebase helper is matched only so a portal host can refuse it;
      // everywhere else it passes through untouched, exactly as when the
      // matcher left it out.
      if (pathname.startsWith(FIREBASE_HELPER_PREFIX)) return NextResponse.next()
      return authProviderMiddleware(request)
  }
}

const NOT_FOUND_HEADERS = { "Content-Type": "text/plain; charset=utf-8", "X-Content-Type-Options": "nosniff" }

/**
 * Next's own redirect of `/path/` to `/path`, which next.config.ts turns off
 * so a website host can keep its folders' slashes. `null` when the path has
 * none to drop.
 */
function withoutTrailingSlash(request: NextRequest): NextResponse | null {
  const { pathname, search } = request.nextUrl
  if (pathname === "/" || !pathname.endsWith("/")) return null
  const target = new URL(`${pathname.replace(/\/+$/, "") || "/"}${search}`, visitorOrigin(request))
  return NextResponse.redirect(target, 308)
}

/**
 * The origin the visitor asked for: the request's host, and the scheme the
 * browser used (a load balancer in front says which). A redirect from here
 * must be an absolute URL, and one built from this server's own view would
 * name plain http behind a balancer.
 */
function visitorOrigin(request: NextRequest): string {
  const scheme = browserScheme({ headers: request.headers, protocol: request.nextUrl.protocol })
  return `${scheme}//${request.headers.get("host") ?? request.nextUrl.host}`
}

/** A request on a host that is no practice's portal: its website, or nothing. */
async function serveSiteHost(request: NextRequest, host: string, hostname: string): Promise<Response> {
  const { pathname, search, protocol } = request.nextUrl
  const found = await lookupSiteHost(hostname)
  const decision = routeSiteHost({ host, hostname, pathname, search, protocol, method: request.method }, found)
  switch (decision.kind) {
    case "serve":
      return fetchSiteFile({
        hostname,
        address: `${visitorOrigin(request)}${decision.pathname}`,
        pathname: decision.pathname,
        method: request.method,
        ifNoneMatch: request.headers.get("if-none-match"),
      })
    case "redirect":
      return NextResponse.redirect(decision.location, 301)
    case "method-not-allowed":
      return new NextResponse(null, { status: 405, headers: { Allow: SITE_METHODS.join(", ") } })
    case "unavailable":
      return new NextResponse("Service Unavailable", {
        status: 503,
        headers: { ...NOT_FOUND_HEADERS, "Retry-After": "5" },
      })
    case "not-found":
      return new NextResponse("Not Found", { status: 404, headers: NOT_FOUND_HEADERS })
  }
}

/**
 * Serve the portal route at `pathname` for a request that arrived at a
 * shorter address on the portal host.
 *
 * The portal route is a public path, so the auth provider passes it through
 * — but it is also what sets the Content-Security-Policy, its nonce and the
 * other security headers, and the portal needs those as much as any page. So
 * the provider sees the request as the portal route it will be served as, and
 * its pass-through becomes a rewrite carrying the same headers.
 */
async function servePortalAt(request: NextRequest, pathname: string) {
  const target = new URL(`${pathname}${request.nextUrl.search}`, request.url)
  const served = await authProviderMiddleware(
    new NextRequest(target, { method: request.method, headers: request.headers }),
  )
  // A public path is never sent anywhere; if the provider ever does, obey it
  // rather than serve a page it refused.
  if (served.headers.has("location")) return served
  return NextResponse.rewrite(target, { headers: served.headers })
}

export const config = {
  matcher: [
    // `__/` is reserved for the Firebase auth helper (/__/auth/*, /__/firebase/*),
    // proxied to the Firebase auth domain in next.config.ts. It must bypass
    // route protection or the OAuth handler 307s to /login and sign-in breaks,
    // so the proxy only ever passes it through — except on a portal host,
    // where it answers 404 (the "/__/:path*" entry below is how it gets here).
    "/((?!_next/static|_next/image|favicon.ico|__/|.*\\.).*)",
    // Files: passed straight through on every host but a practice's website
    // host, which serves its own.
    "/((?!_next/|__/).*\\..*)",
    "/api/login",
    "/api/logout",
    "/_next/static/:path*",
    "/__/:path*",
  ],
}
