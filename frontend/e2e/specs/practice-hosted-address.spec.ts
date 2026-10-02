// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Every practice's hosted addresses, under a hosted domain the deployment names.
 *
 * With `PRACTICE_HOSTED_DOMAIN` set on the backend, and marked as served with
 * `PRACTICE_HOSTED_DOMAIN_READY`, a practice has
 * `{slug}.portal.{domain}` for its portal and `{slug}.{domain}` for its
 * website, with no DNS work of its own. Setting it moves every practice's
 * portal links onto its hosted address, which the specs following the shared
 * practice's links do not expect, so the stack sets it only in a second pass:
 * `docker-compose.e2e-hosted.yml` on top of the usual file, then
 * `--grep @hosted` (`make e2e-hosted`). Without it these specs skip.
 *
 * What only the real stack can prove, through the stand-in load balancer
 * (practice-host-lb) under the hosted names:
 *
 *   1. Settings > Domains shows both addresses; invitation and sign-in
 *      (recovery) links point at the hosted portal address, and a client signs
 *      in from one there.
 *   2. The hosted portal address serves that practice's portal and nothing
 *      else: not the clinician app, not the frontend's own API routes, not
 *      another practice's portal. A slug nobody holds is the same plain 404
 *      as any unknown host.
 *   3. A website published from Settings > Website is live at the hosted
 *      website address, `/portal` there is sent to the portal's address, and
 *      the app's paths are the website's. The portal takes the website's
 *      theme and links back to the hosted website address.
 *   4. Once the practice's own portal host is its working primary, the hosted
 *      portal address sends visitors there.
 *   5. A practice whose address is reserved has no hosted address: its links
 *      stay on the shared portal address and the hosted names answer 404.
 *
 * The rules themselves are pinned in backend/tests/test_portal_hosted.py,
 * tests_integration/database/test_hosted_addresses_db.py and
 * src/lib/portal-host/__tests__/practice-site.test.ts; this spec is the wiring.
 */

import { type APIRequestContext, type Browser, chromium } from "@playwright/test"
import JSZip from "jszip"
import type { ApiClient } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"
import { signInToFreshPractice } from "../fixtures/freshPractice"
import { firstLink, mail } from "../fixtures/mail"
import { givePortalContactDetails, givePortalInvitation, signInFromLink } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL, BASE_URL, PRACTICE_HOST_PORT } from "../fixtures/stack"

/** The hosted domain docker-compose.e2e-hosted.yml names. */
const DOMAIN = "e2e-hosted.example"
const BALANCER_URL = `http://127.0.0.1:${PRACTICE_HOST_PORT}`
/** The domains practice's own portal host (backend/scripts/e2e_seed_practice_domains.py). */
const OWN_PORTAL = "portal.e2e-practice.example"
/** The portal address e2e_seed_second_practice.py gives the reserved practice. */
const RESERVED_SLUG = "status"

const ACCENT = "#24504c"
const THEME = {
  version: 1,
  colors: { accent: ACCENT, accentText: "#ffffff", background: "#fbf8f3", text: "#1d2726" },
  fonts: { heading: "Fraunces", body: "Inter" },
  radius: "lg",
}

interface Hosted {
  portal_host: string
  portal_on: boolean
  site_host: string
  site_live: boolean
}

/** The practice's portal on, its address minted, and its hosted addresses; skips with none. */
async function hostedAddresses(api: ApiClient): Promise<{ slug: string; hosted: Hosted }> {
  await api.put("/api/portal/settings", { enabled: true })
  const { slug } = await api.post<{ slug: string }>("/api/portal/practice-slug")
  const { hosted } = await api.get<{ hosted: Hosted | null }>("/api/practice/domains")
  test.skip(hosted === null, "the stack names no hosted domain; run make e2e-hosted")
  return { slug, hosted: hosted as Hosted }
}

function onHost(request: APIRequestContext, host: string, path: string) {
  return request.get(`${BALANCER_URL}${path}`, {
    headers: { Host: `${host}:${PRACTICE_HOST_PORT}` },
    maxRedirects: 0,
    failOnStatusCode: false,
  })
}

/** A browser that finds the hosted names on this machine; the stack is plain http on the balancer's port. */
function hostedBrowser(): Promise<Browser> {
  return chromium.launch({ args: [`--host-resolver-rules=MAP *.${DOMAIN} 127.0.0.1`] })
}

async function siteZip(marker: string): Promise<{ name: string; mimeType: string; buffer: Buffer }> {
  const zip = new JSZip()
  zip.file("index.html", `<!doctype html><h1>${marker}</h1>`)
  zip.file("portal/index.html", "<h1>Not the portal</h1>")
  zip.file("theme.json", JSON.stringify(THEME))
  return { name: "site.zip", mimeType: "application/zip", buffer: await zip.generateAsync({ type: "nodebuffer" }) }
}

/** The theme and the link back to the website on the hosted portal's landing, read from a fresh load. */
async function portalLook(browser: Browser, origin: string) {
  const page = await browser.newPage()
  try {
    await page.goto(`${origin}/`)
    await expect(page.getByTestId("portal-shell-no-session")).toBeVisible()
    return await page.evaluate(() => {
      const scope = document.querySelector("[data-practice-theme]")
      return {
        accent: scope ? getComputedStyle(scope).getPropertyValue("--primary").trim() : null,
        backToSite: document.querySelector("[data-testid=portal-back-to-site]")?.getAttribute("href") ?? null,
      }
    })
  } finally {
    await page.close()
  }
}

test("a website is live at the hosted website address, and the hosted portal takes its theme @hosted @portal", async ({
  browser,
  request,
}) => {
  const practice = await signInToFreshPractice(browser, "hosted")
  const { page, context, api } = practice
  const onThisMachine = await hostedBrowser()
  const marker = `Hosted ${Date.now()}`
  try {
    const { slug } = await hostedAddresses(api)
    const siteHost = `${slug}.${DOMAIN}`
    const portalOrigin = `http://${slug}.portal.${DOMAIN}:${PRACTICE_HOST_PORT}`

    // First in the file, so the hosted portal is first asked about after this
    // publish and the answer the web app keeps for a minute already knows it.
    await page.goto("/dashboard/settings/website")
    await page.getByLabel("Website zip").setInputFiles(await siteZip(marker))
    await expect(page.getByTestId("website-draft")).toContainText("3 files")
    await page.getByRole("button", { name: "Publish" }).click()
    await expect(page.getByTestId("website-live")).toContainText(`Live at ${siteHost}`)

    await expect
      .poll(async () => (await onHost(request, siteHost, "/")).text(), {
        message: "the hosted website address serves what was published",
        timeout: 90_000,
        intervals: [500, 1_000, 2_000],
      })
      .toContain(marker)

    // The website's own /portal page is never served: the portal lives on its own origin.
    const portal = await onHost(request, siteHost, "/portal/forms?from=site")
    expect(portal.status()).toBe(301)
    expect(portal.headers()["location"]).toBe(`https://${slug}.portal.${DOMAIN}/forms?from=site`)
    // The app's paths are the website's here, and it has none of them.
    for (const path of ["/dashboard", "/login", "/api/logout"]) {
      const response = await onHost(request, siteHost, path)
      expect(response.status(), `${path} on the hosted website address`).toBe(404)
      expect(response.headers()["set-cookie"]).toBeUndefined()
    }

    // The hosted portal wears the website's theme and links back to the hosted website.
    await expect
      .poll(async () => portalLook(onThisMachine, portalOrigin), {
        message: "the hosted portal takes the website's theme and links back to it",
        timeout: 90_000,
        intervals: [1_000, 2_000, 5_000],
      })
      .toEqual({ accent: ACCENT, backToSite: `https://${siteHost}` })

    await page.goto("/dashboard/settings/domains")
    await expect(page.getByTestId("hosted-address-site")).toContainText("Active")
  } finally {
    await onThisMachine.close()
    await context.close()
  }
})

test("a practice's hosted portal address serves its portal, and its links go there @hosted @portal", async ({
  browser,
  request,
}) => {
  const practice = await signInToFreshPractice(browser, "hosted")
  const onThisMachine = await hostedBrowser()
  try {
    const { slug, hosted } = await hostedAddresses(practice.api)
    const portalHost = `${slug}.portal.${DOMAIN}`
    const origin = `http://${portalHost}:${PRACTICE_HOST_PORT}`
    expect(hosted.portal_host).toBe(portalHost)
    expect(hosted.site_host).toBe(`${slug}.${DOMAIN}`)

    await practice.page.goto("/dashboard/settings/domains")
    const portalRow = practice.page.getByTestId("hosted-address-portal")
    await expect(portalRow).toContainText(portalHost)
    await expect(portalRow).toContainText("Active")
    // Published by the test above.
    await expect(practice.page.getByTestId("hosted-address-site")).toContainText("Active")

    const home = await onHost(request, portalHost, "/")
    expect(home.status(), "the hosted portal address's root").toBe(200)
    expect(home.headers()["content-type"]).toContain("text/html")
    const config = await (await onHost(request, portalHost, "/api/config")).json()
    expect(config.apiUrl, "the page calls the API on its own origin").toBe(origin)

    const { email, phone } = givePortalContactDetails()
    const patient = await givePatient(practice.api, { email, phone })
    const invitation = await givePortalInvitation(practice.api, patient.id, email, phone)
    expect(invitation.link, "the invitation is the root of the hosted portal address").toMatch(
      new RegExp(`^https://${portalHost.replaceAll(".", "\\.")}/#invite=`),
    )

    // A sign-in link asked for later goes to the same address.
    const lettersBefore = (await mail.received()).filter((m) => m.to.includes(email)).length
    const asked = await request.post(`${BACKEND_URL}/api/portal/practices/${slug}/recover`, { data: { email } })
    expect(asked.status()).toBe(202)
    await expect
      .poll(async () => (await mail.received()).filter((m) => m.to.includes(email)).length, { timeout: 10_000 })
      .toBeGreaterThan(lettersBefore)
    const recovered = firstLink(await mail.waitFor(email))
    expect(recovered).not.toBe(invitation.link)
    expect(recovered, "the sign-in link is on the hosted portal address").toMatch(
      new RegExp(`^https://${portalHost.replaceAll(".", "\\.")}/`),
    )

    const page = await onThisMachine.newPage()
    await signInFromLink(page, invitation.link.replace(`https://${portalHost}`, origin), invitation.phone)
    await expect(page.getByTestId("portal-shell-practice-name")).toBeVisible()
    await expect(page).toHaveURL(`${origin}/`)
  } finally {
    await onThisMachine.close()
    await practice.context.close()
  }
})

test("a hosted portal address serves its own practice's portal and nothing else @hosted @portal", async ({
  api: sharedApi,
  browser,
  request,
}) => {
  const practice = await signInToFreshPractice(browser, "hosted")
  try {
    const { slug } = await hostedAddresses(practice.api)
    const portalHost = `${slug}.portal.${DOMAIN}`
    const { slug: otherSlug } = await sharedApi.post<{ slug: string }>("/api/portal/practice-slug")
    expect(otherSlug).not.toBe(slug)

    const refused = [
      "/dashboard",
      "/login",
      "/book/anything",
      "/onboarding",
      "/api/login",
      "/api/logout",
      `/portal/${otherSlug}`,
      `/portal/${otherSlug}/forms`,
    ]
    for (const path of refused) {
      const response = await onHost(request, portalHost, path)
      expect(response.status(), `${path} on the hosted portal address`).toBe(404)
      expect(response.headers()["location"], `${path} is not a redirect`).toBeUndefined()
    }

    // A slug nobody holds says no more than a host nobody has heard of.
    const unknown = await onHost(request, `unknown-${Date.now()}.e2e-practice.example`, "/")
    const nobody = `nobody-${Date.now()}`
    for (const host of [`${nobody}.portal.${DOMAIN}`, `${nobody}.${DOMAIN}`]) {
      const response = await onHost(request, host, "/")
      expect(response.status(), host).toBe(unknown.status())
      expect(response.status()).toBe(404)
      expect(response.headers()["content-type"], host).toBe(unknown.headers()["content-type"])
      expect(await response.text(), host).toBe(await unknown.text())
    }
  } finally {
    await practice.context.close()
  }
})

test("the hosted portal address sends visitors to the practice's own working primary @hosted @portal", async ({
  browser,
  request,
}) => {
  const practice = await signInToFreshPractice(browser, "domains")
  try {
    const { slug } = await hostedAddresses(practice.api)
    await practice.api.post(`/api/practice/domains/${OWN_PORTAL}/primary`)

    const response = await onHost(request, `${slug}.portal.${DOMAIN}`, "/forms?from=email")
    expect(response.status()).toBe(301)
    expect(response.headers()["location"]).toBe(`https://${OWN_PORTAL}/forms?from=email`)

    await practice.page.goto("/dashboard/settings/domains")
    await expect(practice.page.getByTestId("hosted-address-portal")).toContainText(
      `Sends visitors to ${OWN_PORTAL}.`,
    )
  } finally {
    await practice.context.close()
  }
})

test("a practice whose address is reserved keeps the shared portal address @hosted @portal", async ({
  browser,
  request,
}) => {
  // Only meaningful with a hosted domain, which the hosted practice shows.
  const probe = await signInToFreshPractice(browser, "hosted")
  try {
    await hostedAddresses(probe.api)
  } finally {
    await probe.context.close()
  }

  const practice = await signInToFreshPractice(browser, "reserved")
  try {
    await practice.api.put("/api/portal/settings", { enabled: true })
    const { slug } = await practice.api.post<{ slug: string }>("/api/portal/practice-slug")
    expect(slug).toBe(RESERVED_SLUG)
    const { hosted } = await practice.api.get<{ hosted: Hosted | null }>("/api/practice/domains")
    expect(hosted, "no hosted address for a reserved slug").toBeNull()

    const { email, phone } = givePortalContactDetails()
    const patient = await givePatient(practice.api, { email, phone })
    const invitation = await givePortalInvitation(practice.api, patient.id, email, phone)
    expect(invitation.link.startsWith(`${BASE_URL}/portal/${RESERVED_SLUG}#invite=`), invitation.link).toBe(true)

    for (const host of [`${RESERVED_SLUG}.portal.${DOMAIN}`, `${RESERVED_SLUG}.${DOMAIN}`]) {
      expect((await onHost(request, host, "/")).status(), host).toBe(404)
    }
  } finally {
    await practice.context.close()
  }
})
