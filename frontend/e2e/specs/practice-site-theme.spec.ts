// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice's portal, on the practice's own host, wearing the theme its
 * website's `theme.json` declares.
 *
 * The domains practice holds working website and portal hosts
 * (backend/scripts/e2e_seed_practice_domains.py). Through the real Settings >
 * Website page, the owner publishes a site with no theme.json, then one with
 * a theme.json, and a browser that resolves the practice's hosts to this
 * machine opens the portal on its primary host after each:
 *
 *   1. With no theme the portal keeps its own look — no themed element, the
 *      app's own background — but, being on the practice's own host, no
 *      "Powered by Pablo" either.
 *   2. The draft's theme.json is reported on the Website page: what it gives
 *      the portal, and the value it leaves out (too little contrast) and why.
 *   3. Once published, the portal on the practice's host takes the theme's
 *      accent, background and fonts — read from the computed style — and the
 *      fonts load from this app, not from anywhere else.
 *
 * The rules are pinned in unit tests: reading and checking theme.json in
 * backend/tests/test_site_theme.py, storing it with a version and rolling back
 * in backend/tests_integration/database/test_practice_sites_db.py, and turning
 * it into CSS in src/lib/portal-host/__tests__/practice-theme.test.ts. This
 * spec is the wiring.
 */

import { type Browser, type Page, chromium } from "@playwright/test"
import JSZip from "jszip"
import type { ApiClient } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"
import { signInToFreshPractice } from "../fixtures/freshPractice"
import { PRACTICE_HOST_PORT } from "../fixtures/stack"

const PRIMARY = "portal.e2e-practice.example"
const PRIMARY_ORIGIN = `http://${PRIMARY}:${PRACTICE_HOST_PORT}`
const SETTINGS_PATH = "/dashboard/settings/website"

const ACCENT = "#24504c"
const BACKGROUND = "#fbf8f3"
const THEME = {
  version: 1,
  colors: { accent: ACCENT, accentText: "#ffffff", background: BACKGROUND, text: "#1d2726", mutedText: "#dddddd" },
  fonts: { heading: "Fraunces", body: "Inter" },
  radius: "lg",
}

/** The domains practice with its portal on, its address minted, and PRIMARY its primary. */
async function domainsPractice(api: ApiClient): Promise<void> {
  await api.put("/api/portal/settings", { enabled: true })
  await api.post("/api/portal/practice-slug")
  await api.post(`/api/practice/domains/${PRIMARY}/primary`)
}

async function siteZip(marker: string, theme?: object) {
  const zip = new JSZip()
  zip.file("index.html", `<!doctype html><h1>${marker}</h1>`)
  if (theme) zip.file("theme.json", JSON.stringify(theme))
  return { name: "site.zip", mimeType: "application/zip", buffer: await zip.generateAsync({ type: "nodebuffer" }) }
}

async function publish(page: Page, marker: string, theme?: object): Promise<void> {
  await page.getByLabel("Website zip").setInputFiles(await siteZip(marker, theme))
  await expect(page.getByTestId("website-draft")).toContainText(theme ? "2 files" : "1 file,")
  await page.getByRole("button", { name: "Publish" }).click()
  await expect(page.getByTestId("website-draft")).toHaveText("No draft yet.")
}

/** What the portal on the practice's host looks like, read from a fresh load. */
async function portalLook(browser: Browser) {
  const page = await browser.newPage()
  try {
    await page.goto(`${PRIMARY_ORIGIN}/`)
    await expect(page.getByTestId("portal-shell-no-session")).toBeVisible()
    return await page.evaluate(async () => {
      await document.fonts.ready
      const scope = document.querySelector("[data-practice-theme]")
      const shell = document.querySelector("main")?.parentElement
      const heading = document.querySelector("h2")
      return {
        themed: scope !== null,
        accent: scope ? getComputedStyle(scope).getPropertyValue("--primary").trim() : null,
        background: shell ? getComputedStyle(shell).backgroundColor : null,
        heading: heading ? getComputedStyle(heading).fontFamily : null,
        body: shell ? getComputedStyle(shell).fontFamily : null,
        interLoaded: document.fonts.check('16px "Inter"'),
        poweredByPablo: document.body.innerText.includes("Powered by Pablo"),
      }
    })
  } finally {
    await page.close()
  }
}

test("the portal on a practice's own host wears its website's theme @portal", async ({ browser }) => {
  const practice = await signInToFreshPractice(browser, "domains")
  // A browser that finds the practice's hosts on this machine, as in
  // practice-own-host.spec.ts.
  const onThisMachine = await chromium.launch({
    args: ["--host-resolver-rules=MAP *.e2e-practice.example 127.0.0.1"],
  })
  const fontRequests: string[] = []
  try {
    await domainsPractice(practice.api)
    const { page } = practice
    await page.goto(SETTINGS_PATH)

    // 1. No theme.json: the portal in its own look.
    await publish(page, `Plain ${Date.now()}`)
    await expect
      .poll(async () => (await portalLook(onThisMachine)).themed, {
        message: "the portal has no theme",
        timeout: 90_000,
        intervals: [1_000, 2_000, 5_000],
      })
      .toBe(false)
    const plain = await portalLook(onThisMachine)
    expect(plain.background).not.toBe("rgb(251, 248, 243)")
    expect(plain.poweredByPablo, "no Powered by Pablo on the practice's own host, theme or not").toBe(false)

    // 2. The draft's theme.json, as the Website page reports it.
    await page.getByLabel("Website zip").setInputFiles(await siteZip(`Themed ${Date.now()}`, THEME))
    const report = page.getByTestId("website-theme")
    await expect(report).toContainText(
      "Once this is published, your portal on your own domain will use the colors, fonts and corner style from theme.json.",
    )
    await expect(report.getByRole("listitem")).toHaveText(
      "colors.mutedText: Too little contrast with background to read easily.",
    )
    await page.getByRole("button", { name: "Publish" }).click()
    await expect(page.getByTestId("website-draft")).toHaveText("No draft yet.")

    // 3. The portal on the practice's host, themed.
    await expect
      .poll(async () => (await portalLook(onThisMachine)).accent, {
        message: "the portal takes the website's accent",
        timeout: 90_000,
        intervals: [1_000, 2_000, 5_000],
      })
      .toBe(ACCENT)

    const watched = await onThisMachine.newPage()
    watched.on("request", (sent) => {
      if (sent.resourceType() === "font") fontRequests.push(sent.url())
    })
    await watched.goto(`${PRIMARY_ORIGIN}/`)
    await expect(watched.getByTestId("portal-shell-no-session")).toBeVisible()
    await watched.close()

    const themed = await portalLook(onThisMachine)
    expect(themed.background, "the theme's background").toBe("rgb(251, 248, 243)")
    expect(themed.heading).toContain("Fraunces")
    expect(themed.body).toContain("Inter")
    expect(themed.interLoaded, "the body font loaded").toBe(true)
    expect(themed.poweredByPablo).toBe(false)
    expect(fontRequests.length, "the page asked for its fonts").toBeGreaterThan(0)
    for (const url of fontRequests) {
      expect(new URL(url).origin, "every font came from the practice's own origin").toBe(PRIMARY_ORIGIN)
    }
  } finally {
    await onThisMachine.close()
    await practice.context.close()
  }
})
