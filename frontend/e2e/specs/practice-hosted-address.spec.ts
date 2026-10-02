// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Every practice's hosted addresses, under a hosted domain the deployment names.
 *
 * With `PRACTICE_HOSTED_DOMAIN` set on the backend, a practice has
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
 *   1. Settings > Domains shows both addresses, and invitation links point at
 *      the hosted portal address; a client signs in from one there.
 *   2. A website published from Settings > Website is live at the hosted
 *      website address, and `/portal` there is sent to the portal's address.
 *   3. Once the practice's own portal host is its working primary, the hosted
 *      portal address sends visitors there.
 *
 * The rules themselves are pinned in backend/tests/test_portal_hosted.py,
 * tests_integration/database/test_hosted_addresses_db.py and
 * src/lib/portal-host/__tests__/practice-site.test.ts; this spec is the wiring.
 */

import { type APIRequestContext, chromium } from "@playwright/test"
import JSZip from "jszip"
import type { ApiClient } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"
import { signInToFreshPractice } from "../fixtures/freshPractice"
import { givePortalContactDetails, givePortalInvitation, signInFromLink } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { PRACTICE_HOST_PORT } from "../fixtures/stack"

/** The hosted domain docker-compose.e2e-hosted.yml names. */
const DOMAIN = "e2e-hosted.example"
const BALANCER_URL = `http://127.0.0.1:${PRACTICE_HOST_PORT}`
/** The domains practice's own portal host (backend/scripts/e2e_seed_practice_domains.py). */
const OWN_PORTAL = "portal.e2e-practice.example"

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

async function siteZip(marker: string): Promise<{ name: string; mimeType: string; buffer: Buffer }> {
  const zip = new JSZip()
  zip.file("index.html", `<!doctype html><h1>${marker}</h1>`)
  zip.file("portal/index.html", "<h1>Not the portal</h1>")
  return { name: "site.zip", mimeType: "application/zip", buffer: await zip.generateAsync({ type: "nodebuffer" }) }
}

test("a practice's hosted portal address serves its portal, and its links go there @hosted @portal", async ({
  browser,
  request,
}) => {
  const practice = await signInToFreshPractice(browser, "hosted")
  // A browser that finds the hosted names on this machine. The stack is plain
  // http on the balancer's published port; a deployed address is https.
  const onThisMachine = await chromium.launch({ args: [`--host-resolver-rules=MAP *.${DOMAIN} 127.0.0.1`] })
  try {
    const { slug, hosted } = await hostedAddresses(practice.api)
    const portalHost = `${slug}.portal.${DOMAIN}`
    expect(hosted.portal_host).toBe(portalHost)
    expect(hosted.site_host).toBe(`${slug}.${DOMAIN}`)

    await practice.page.goto("/dashboard/settings/domains")
    const portalRow = practice.page.getByTestId("hosted-address-portal")
    await expect(portalRow).toContainText(portalHost)
    await expect(portalRow).toContainText("Active")
    await expect(practice.page.getByTestId("hosted-address-site")).toContainText(
      "Works once you publish your website.",
    )

    const home = await onHost(request, portalHost, "/")
    expect(home.status(), "the hosted portal address's root").toBe(200)
    expect(home.headers()["content-type"]).toContain("text/html")
    const config = await (await onHost(request, portalHost, "/api/config")).json()
    expect(config.apiUrl, "the page calls the API on its own origin").toBe(`http://${portalHost}:${PRACTICE_HOST_PORT}`)
    for (const path of ["/dashboard", "/login"]) {
      expect((await onHost(request, portalHost, path)).status(), `${path} on the portal address`).toBe(404)
    }

    const { email, phone } = givePortalContactDetails()
    const patient = await givePatient(practice.api, { email, phone })
    const invitation = await givePortalInvitation(practice.api, patient.id, email, phone)
    expect(invitation.link, "the link is the root of the hosted portal address").toMatch(
      new RegExp(`^https://${portalHost.replaceAll(".", "\\.")}/#invite=`),
    )

    const page = await onThisMachine.newPage()
    const origin = `http://${portalHost}:${PRACTICE_HOST_PORT}`
    await signInFromLink(page, invitation.link.replace(`https://${portalHost}`, origin), invitation.phone)
    await expect(page.getByTestId("portal-shell-practice-name")).toBeVisible()
    await expect(page).toHaveURL(`${origin}/`)
  } finally {
    await onThisMachine.close()
    await practice.context.close()
  }
})

test("a website is live at the hosted website address, and /portal there leads to the portal @hosted @portal", async ({
  browser,
  request,
}) => {
  const practice = await signInToFreshPractice(browser, "hosted")
  const { page, context, api } = practice
  const marker = `Hosted ${Date.now()}`
  try {
    const { slug } = await hostedAddresses(api)
    const siteHost = `${slug}.${DOMAIN}`

    await page.goto("/dashboard/settings/website")
    await page.getByLabel("Website zip").setInputFiles(await siteZip(marker))
    await expect(page.getByTestId("website-draft")).toContainText("2 files")
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

    await page.goto("/dashboard/settings/domains")
    await expect(page.getByTestId("hosted-address-site")).toContainText("Active")
  } finally {
    await context.close()
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
