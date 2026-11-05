// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The logout path's navigation answer. The cookie headers on the redirect
 * must be the library's own, header for header: the flags a cookie is
 * expired with have to match the ones it was set with, or the browser keeps
 * it. The reason forwarded onto /login is the only other thing it decides.
 */
import { describe, it, expect } from "vitest"
import { NextRequest } from "next/server"
import { isLogoutNavigation, loginRedirectAfterLogout } from "../logout-navigation"

// What next-firebase-auth-edge appends with `enableMultipleCookies` on.
const EXPIRED_COOKIES = ["id", "refresh", "custom", "metadata", "sig"].map(
  (part) =>
    `AuthToken.${part}=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; Secure; SameSite=Lax`
)

function logoutRequest(search = "") {
  return new NextRequest(`https://example.test/api/logout${search}`)
}

describe("isLogoutNavigation", () => {
  it("is true for a browser navigation", () => {
    expect(isLogoutNavigation(new Headers({ "sec-fetch-mode": "navigate" }))).toBe(true)
  })

  it("is false for a fetch() from script", () => {
    expect(isLogoutNavigation(new Headers({ "sec-fetch-mode": "cors" }))).toBe(false)
    expect(
      isLogoutNavigation(new Headers({ "sec-fetch-mode": "cors", accept: "text/html" }))
    ).toBe(false)
  })

  it("falls back to what the request accepts when the browser does not say", () => {
    expect(
      isLogoutNavigation(
        new Headers({ accept: "text/html,application/xhtml+xml,*/*;q=0.8" })
      )
    ).toBe(true)
    expect(isLogoutNavigation(new Headers({ accept: "application/json" }))).toBe(false)
    expect(isLogoutNavigation(new Headers())).toBe(false)
  })
})

describe("loginRedirectAfterLogout", () => {
  it("redirects to /login on the request's origin with every cookie header as given", () => {
    const response = loginRedirectAfterLogout(logoutRequest(), EXPIRED_COOKIES)

    expect(response.status).toBe(307)
    expect(response.headers.get("location")).toBe("https://example.test/login")
    expect(response.headers.getSetCookie()).toEqual(EXPIRED_COOKIES)
  })

  it("forwards a reason the login screen knows", () => {
    const response = loginRedirectAfterLogout(
      logoutRequest("?reason=session_expired"),
      EXPIRED_COOKIES
    )

    expect(response.headers.get("location")).toBe(
      "https://example.test/login?reason=session_expired"
    )
  })

  it("drops any other query", () => {
    const response = loginRedirectAfterLogout(
      logoutRequest("?reason=evil&returnTo=%2Fdashboard"),
      EXPIRED_COOKIES
    )

    expect(response.headers.get("location")).toBe("https://example.test/login")
  })

  it("carries no cookie header when it was given none", () => {
    const response = loginRedirectAfterLogout(logoutRequest(), [])

    expect(response.headers.getSetCookie()).toEqual([])
  })
})
