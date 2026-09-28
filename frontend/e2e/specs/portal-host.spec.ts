// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient portal on a host of its own.
 *
 * This stack names 127.0.0.1 as the portal host (the frontend's
 * PORTAL_HOSTS), so one server is the clinician app at localhost and the
 * portal at 127.0.0.1. Only a browser against the real server can prove the
 * parts that matter:
 *
 *   1. `/{slug}` on the portal host is the portal — the shell resolves the
 *      practice and a patient signs in there from an invitation link.
 *   2. The session lands in the portal origin's storage, not the clinician
 *      app's. That separation is the reason the portal host exists.
 *   3. The clinician app is not served on the portal host at all: its pages
 *      and its frontend API routes answer 404, not a sign-in redirect —
 *      while nothing the portal page itself calls is refused.
 *   4. A `/portal/...` link on the clinician host — every link already sent —
 *      is permanently redirected to the portal host, query and all.
 *
 * The routing rules themselves are pinned in
 * src/lib/portal-host/__tests__/routing.test.ts; this spec is the wiring.
 */

import { expect, test } from "../fixtures/auth"
import { givePortalContactDetails, givePortalInvitation, signInToPortal } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BASE_URL, PORTAL_URL } from "../fixtures/stack"

/** The portal host's form of a `/portal/{slug}...` link: same path minus the prefix, fragment kept. */
function onPortalHost(link: string): string {
  const url = new URL(link)
  const path = url.pathname.replace(/^\/portal(?=\/|$)/, "") || "/"
  return `${PORTAL_URL}${path}${url.search}${url.hash}`
}

function slugOf(link: string): string {
  return new URL(link).pathname.split("/")[2]
}

test("a practice's portal renders at /{slug} on the portal host @portal", async ({ api, page }) => {
  const { slug } = await api.post<{ slug: string }>("/api/portal/practice-slug")

  await page.goto(`${PORTAL_URL}/${slug}`)

  await expect(page.getByTestId("portal-shell-practice-name")).toBeVisible()
  await expect(page.getByTestId("portal-shell-no-session")).toBeVisible()
  // Served in place, not bounced: the address the patient typed is the one
  // they stay on.
  expect(page.url()).toBe(`${PORTAL_URL}/${slug}`)
})

test("a patient signs in on the portal host, and the session stays there @portal", async ({ api, page }) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const invitation = await givePortalInvitation(api, patient.id, email, phone)
  const slug = slugOf(invitation.link)
  const link = onPortalHost(invitation.link)
  expect(link, "the rewritten link keeps its invitation fragment").toContain("#")

  // The portal host refuses the frontend's other API routes, so a page that
  // quietly relied on one would break there. Nothing the portal calls may.
  //
  // One refusal is expected and harmless: the app-wide clinician auth
  // provider, finding no clinician signed in, clears its session cookie with a
  // best-effort `/api/logout`. There is no such cookie on the portal's origin
  // to clear, and the provider ignores the answer.
  const EXPECTED_REFUSALS = new Set([`${PORTAL_URL}/api/logout`])
  const refused: string[] = []
  page.on("response", (response) => {
    const url = response.url()
    if (url.startsWith(`${PORTAL_URL}/api/`) && response.status() === 404 && !EXPECTED_REFUSALS.has(url)) {
      refused.push(url)
    }
  })

  await signInToPortal(page, { ...invitation, link })
  expect(refused, "no frontend API call from the portal page is refused").toEqual([])

  // The shell spends the fragment and takes it back out of the address bar,
  // leaving the portal host's own short address.
  await expect(page).toHaveURL(`${PORTAL_URL}/${slug}`)

  const key = `pablo-portal-session:${slug}`
  const onPortal = await page.evaluate((k) => window.localStorage.getItem(k), key)
  expect(onPortal, "the session is stored under the portal origin").toBeTruthy()

  await page.goto(`${BASE_URL}/login`)
  const onClinician = await page.evaluate((k) => window.localStorage.getItem(k), key)
  expect(onClinician, "the clinician app's origin holds no portal session").toBeNull()
})

test("the clinician app is not served on the portal host @portal", async ({ request }) => {
  for (const path of ["/dashboard", "/login", "/", "/api/login", "/api/auth/exchange-setup-token"]) {
    const response = await request.get(`${PORTAL_URL}${path}`, { maxRedirects: 0, failOnStatusCode: false })
    expect(response.status(), `${path} on the portal host`).toBe(404)
    expect(response.headers()["location"], `${path} is not a redirect`).toBeUndefined()
  }
})

test("a /portal link on the clinician host moves to the portal host @portal", async ({ api, request }) => {
  const { slug } = await api.post<{ slug: string }>("/api/portal/practice-slug")

  const response = await request.get(`${BASE_URL}/portal/${slug}/recover?from=email`, {
    maxRedirects: 0,
    failOnStatusCode: false,
  })

  expect(response.status()).toBe(301)
  expect(response.headers()["location"]).toBe(`${PORTAL_URL}/${slug}/recover?from=email`)
})
