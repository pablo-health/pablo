// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice's portal on the practice's own host.
 *
 * The domains practice holds two working portal hosts and one still pending
 * (backend/scripts/e2e_seed_practice_domains.py). This stack turns host
 * lookups on (the frontend's APP_HOSTS), so the one frontend server serves a
 * request according to the Host it carries. What only the real stack can
 * prove:
 *
 *   1. The practice's primary host serves its portal at the root — the real
 *      proxy asked the real backend which practice the host belongs to.
 *   2. An alias sends visitors to the primary, the practice's own
 *      `/portal/{slug}` address moves to the root, and another practice's
 *      portal, the clinician app, a pending host and an unknown host all
 *      answer 404.
 *   3. Through a balancer that splits `/api` the way a deployed one does
 *      (practice-host-lb), the page is told to call the API on its own
 *      origin, and does.
 *   4. The practice's invitation links point at the root of its primary
 *      host, and a client signs in from one there.
 *
 * The requests in (1) to (3) set the Host header directly. A browser cannot
 * do that, so (4) runs a browser of its own that resolves the practice's
 * hosts to this machine.
 *
 * The routing rules themselves are pinned in
 * src/lib/portal-host/__tests__/practice-host.test.ts; this spec is the wiring.
 */

import { type APIRequestContext, chromium } from "@playwright/test"
import type { ApiClient } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"
import { signInToFreshPractice } from "../fixtures/freshPractice"
import { givePortalContactDetails, givePortalInvitation, signInFromLink } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BASE_URL, PRACTICE_HOST_PORT } from "../fixtures/stack"

const PRIMARY = "portal.e2e-practice.example"
const ALIAS = "clients.e2e-practice.example"
const PENDING = "pending.e2e-practice.example"
/** The primary as a browser reaches it on this stack: through the balancer, plain http. */
const PRIMARY_ORIGIN = `http://${PRIMARY}:${PRACTICE_HOST_PORT}`
const BALANCER_URL = `http://127.0.0.1:${PRACTICE_HOST_PORT}`

/** The domains practice with its portal on, its address minted, and PRIMARY its primary. */
async function domainsPractice(api: ApiClient): Promise<string> {
  await api.put("/api/portal/settings", { enabled: true })
  const { slug } = await api.post<{ slug: string }>("/api/portal/practice-slug")
  await api.post(`/api/practice/domains/${PRIMARY}/primary`)
  return slug
}

function onHost(request: APIRequestContext, host: string, path: string, base = BASE_URL) {
  return request.get(`${base}${path}`, {
    headers: { Host: host },
    maxRedirects: 0,
    failOnStatusCode: false,
  })
}

test("a practice's own host serves its portal and nothing else @portal", async ({
  api: sharedApi,
  browser,
  request,
}) => {
  const practice = await signInToFreshPractice(browser, "domains")
  try {
    const slug = await domainsPractice(practice.api)
    const { slug: otherSlug } = await sharedApi.post<{ slug: string }>("/api/portal/practice-slug")
    expect(otherSlug).not.toBe(slug)

    const home = await onHost(request, PRIMARY, "/")
    expect(home.status(), "the primary host's root").toBe(200)
    expect(home.headers()["content-type"]).toContain("text/html")
    expect(home.headers()["location"]).toBeUndefined()

    const alias = await onHost(request, ALIAS, "/forms?from=email")
    expect(alias.status(), "an alias").toBe(301)
    expect(alias.headers()["location"]).toBe(`https://${PRIMARY}/forms?from=email`)

    const own = await onHost(request, PRIMARY, `/portal/${slug}/recover?from=email`)
    expect(own.status(), "the practice's own /portal address").toBe(301)
    expect(own.headers()["location"]).toBe(`http://${PRIMARY}/recover?from=email`)

    const refused: [string, string][] = [
      [PRIMARY, `/portal/${otherSlug}`],
      [PRIMARY, "/dashboard"],
      [PRIMARY, "/login"],
      [PRIMARY, "/api/logout"],
      [PENDING, "/"],
      [PENDING, "/api/config"],
      [`unknown-${Date.now()}.e2e-practice.example`, "/"],
    ]
    for (const [host, path] of refused) {
      const response = await onHost(request, host, path)
      expect(response.status(), `${path} on ${host}`).toBe(404)
      expect(response.headers()["location"], `${path} on ${host} is not a redirect`).toBeUndefined()
    }

    // The app's own host is served exactly as before, and keeps its API address.
    // (A signed-in clinician is sent on from /login; what matters is that the
    // app answers rather than the 404 a practice's host would give.)
    const login = await onHost(request, new URL(BASE_URL).host, "/login")
    expect([200, 307]).toContain(login.status())
    const appConfig = await (await onHost(request, new URL(BASE_URL).host, "/api/config")).json()
    expect(appConfig.apiUrl).not.toContain(PRIMARY)
  } finally {
    await practice.context.close()
  }
})

test("the practice's own host is told to call the API on its own origin @portal", async ({
  browser,
  request,
}) => {
  const practice = await signInToFreshPractice(browser, "domains")
  try {
    const slug = await domainsPractice(practice.api)
    const host = `${PRIMARY}:${PRACTICE_HOST_PORT}`

    const config = await onHost(request, host, "/api/config", BALANCER_URL)
    expect(config.status()).toBe(200)
    expect((await config.json()).apiUrl).toBe(PRIMARY_ORIGIN)

    // And the backend answers there: the same origin reaches it.
    const resolved = await onHost(request, host, `/api/portal/practices/${slug}`, BALANCER_URL)
    expect(resolved.status()).toBe(200)
    expect((await resolved.json()).slug).toBe(slug)
  } finally {
    await practice.context.close()
  }
})

test("a client signs in from an invitation on the practice's own host @portal", async ({ browser }) => {
  const practice = await signInToFreshPractice(browser, "domains")
  // A browser that finds the practice's hosts on this machine. The stack is
  // plain http on the balancer's published port; a deployed host is https.
  const onThisMachine = await chromium.launch({
    args: ["--host-resolver-rules=MAP *.e2e-practice.example 127.0.0.1"],
  })
  try {
    await domainsPractice(practice.api)
    const { email, phone } = givePortalContactDetails()
    const patient = await givePatient(practice.api, { email, phone })
    const invitation = await givePortalInvitation(practice.api, patient.id, email, phone)
    expect(invitation.link, "the link is the root of the primary host").toMatch(
      new RegExp(`^https://${PRIMARY.replaceAll(".", "\\.")}/#invite=`),
    )

    const page = await onThisMachine.newPage()
    const apiCalls: string[] = []
    page.on("request", (sent) => {
      const url = new URL(sent.url())
      if (url.pathname.startsWith("/api/") && url.pathname !== "/api/config") apiCalls.push(url.origin)
    })

    await signInFromLink(page, invitation.link.replace(`https://${PRIMARY}`, PRIMARY_ORIGIN), invitation.phone)

    await expect(page.getByTestId("portal-shell-practice-name")).toBeVisible()
    // The shell spends the fragment and leaves the root in the address bar.
    await expect(page).toHaveURL(`${PRIMARY_ORIGIN}/`)
    expect(apiCalls.length, "the page called the API").toBeGreaterThan(0)
    expect(new Set(apiCalls), "every API call stayed on the practice's own origin").toEqual(
      new Set([PRIMARY_ORIGIN]),
    )
  } finally {
    await onThisMachine.close()
    await practice.context.close()
  }
})
