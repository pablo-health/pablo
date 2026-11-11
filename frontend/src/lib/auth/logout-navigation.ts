// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A browser navigation to the logout path lands on /login.
 *
 * The auth library answers the logout path before any token verification,
 * with a JSON body and the Set-Cookie headers that expire the session
 * cookies. That is the right answer for a fetch() from script, and a dead
 * end for a navigation: the user is signed out and looking at
 * `{"success":true}`. So the middleware keeps the library's answer for
 * fetches and, for a navigation, turns it into a redirect to /login carrying
 * the same cookie headers. One response clears the cookies and lands the
 * user, which is what lets sign-out work when the session is already dead
 * and no API call can be relied on.
 */

import { type NextRequest, NextResponse } from "next/server"
import { isForcedLogoutArrival } from "./forced-logout"

/**
 * Whether the request is a top-level navigation (the address bar, a link,
 * `window.location.assign`) rather than a fetch() from script. Browsers say
 * so in `Sec-Fetch-Mode`; one that does not is read by what it will accept.
 */
export function isLogoutNavigation(headers: Headers): boolean {
  const mode = headers.get("sec-fetch-mode")
  if (mode) return mode === "navigate"
  return (headers.get("accept") ?? "").includes("text/html")
}

/**
 * The redirect to /login for a navigation to the logout path.
 *
 * `setCookieHeaders` are the library's cookie-expiry headers, copied onto the
 * redirect verbatim — never re-derived, so the flags stay exactly what the
 * cookie was set with. A `reason` on the logout URL is forwarded only when it
 * is one the login screen knows; anything else is dropped.
 */
export function loginRedirectAfterLogout(
  request: NextRequest,
  setCookieHeaders: readonly string[],
): NextResponse {
  const url = request.nextUrl.clone()
  url.pathname = "/login"
  url.search = isForcedLogoutArrival(request.nextUrl.searchParams)
    ? `reason=${request.nextUrl.searchParams.get("reason")}`
    : ""
  const response = NextResponse.redirect(url)
  for (const header of setCookieHeaders) response.headers.append("set-cookie", header)
  return response
}
