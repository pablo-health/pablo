// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice's portal, on the practice's own host, wearing the header its
 * website's `theme.json` declares.
 *
 * Through the real Settings > Website page the owner uploads a site whose
 * theme.json has a header block with three values that must never reach the
 * portal: a `javascript:` link, a link to a site that is not the practice's,
 * and a label that mixes alphabets. Then:
 *
 *   1. The Website page says the portal will use the header, and lists those
 *      three as left out, each with its reason.
 *   2. Once published, the signed-out landing on the practice's primary host
 *      shows the wordmark and subtitle linking to the website, the one good
 *      link and the call to action, all as addresses on the live website. The
 *      practice's own name is still the page's heading and the header's
 *      accessible name.
 *   3. None of the three left-out values appears anywhere on the page.
 *
 * The rules are pinned in unit tests: backend/tests/test_site_header.py (the
 * backend's reading, every reason word for word),
 * src/lib/portal-host/__tests__/practice-header.test.ts (the portal's second
 * check) and src/components/portal-shell/__tests__/website-header.test.tsx
 * (rendering). This spec is the wiring.
 */

import { type Browser, type Page, chromium } from "@playwright/test"
import JSZip from "jszip"
import type { ApiClient } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"
import { signInToFreshPractice } from "../fixtures/freshPractice"
import { PRACTICE_HOST_PORT } from "../fixtures/stack"

const PRIMARY = "portal.e2e-practice.example"
const PRIMARY_ORIGIN = `http://${PRIMARY}:${PRACTICE_HOST_PORT}`
/** The practice's primary website host (e2e_seed_practice_domains.py). */
const SITE = "e2e-site.example"
const SETTINGS_PATH = "/dashboard/settings/website"

const WORDMARK = "Riverside Counseling"
const SUBTITLE = "Individual and couples therapy"
/** "Services" with a Cyrillic e. */
const MIXED_LABEL = "S\u0435rvices"
const HEADER = {
  wordmark: WORDMARK,
  subtitle: SUBTITLE,
  links: [
    { label: "About", href: "/about/" },
    { label: "Run me", href: "javascript:alert(document.domain)" },
    { label: "Elsewhere", href: "https://elsewhere.example/" },
    { label: MIXED_LABEL, href: "/services" },
  ],
  cta: { label: "Schedule a visit", href: "/#schedule" },
}

async function domainsPractice(api: ApiClient): Promise<void> {
  await api.put("/api/portal/settings", { enabled: true })
  await api.post("/api/portal/practice-slug")
  await api.post(`/api/practice/domains/${PRIMARY}/primary`)
}

async function siteZip(marker: string, theme?: object) {
  const zip = new JSZip()
  zip.file("index.html", `<!doctype html><h1>${marker}</h1>`)
  zip.file("about/index.html", "<!doctype html><h1>About</h1>")
  if (theme) zip.file("theme.json", JSON.stringify(theme))
  return { name: "site.zip", mimeType: "application/zip", buffer: await zip.generateAsync({ type: "nodebuffer" }) }
}

async function publish(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Publish" }).click()
  await expect(page.getByTestId("website-draft")).toHaveText("No draft yet.")
}

/** The header on the practice's host, read from a fresh load. */
async function portalHeader(browser: Browser) {
  const page = await browser.newPage()
  try {
    await page.goto(`${PRIMARY_ORIGIN}/`)
    await expect(page.getByTestId("portal-shell-no-session")).toBeVisible()
    return await page.evaluate(() => {
      const banner = document.querySelector("header")
      const links = [...document.querySelectorAll("a")].map((a) => ({
        text: a.textContent ?? "",
        href: a.getAttribute("href") ?? "",
      }))
      return {
        bannerName: banner?.getAttribute("aria-label") ?? null,
        heading: document.querySelector("h1[data-testid=portal-shell-practice-name]")?.textContent ?? null,
        wordmark: document.querySelector("[data-testid=portal-header-wordmark]")?.textContent ?? null,
        wordmarkHref: document.querySelector("[data-testid=portal-header-wordmark]")?.getAttribute("href") ?? null,
        subtitle: document.querySelector("[data-testid=portal-header-subtitle]")?.textContent ?? null,
        websiteLinks: [...document.querySelectorAll("nav[aria-label='Practice website'] a")].map((a) => ({
          text: a.textContent ?? "",
          href: a.getAttribute("href") ?? "",
        })),
        cta: (() => {
          const cta = document.querySelector("[data-testid=portal-header-cta]")
          return cta ? { text: cta.textContent ?? "", href: cta.getAttribute("href") ?? "" } : null
        })(),
        links,
        text: document.body.innerText,
      }
    })
  } finally {
    await page.close()
  }
}

test("the portal on a practice's own host wears its website's header @portal", async ({ browser }) => {
  const practice = await signInToFreshPractice(browser, "domains")
  const onThisMachine = await chromium.launch({
    args: ["--host-resolver-rules=MAP *.e2e-practice.example 127.0.0.1"],
  })
  try {
    await domainsPractice(practice.api)
    const { page } = practice
    await page.goto(SETTINGS_PATH)

    // 1. The draft's header, as the Website page reports it.
    await page.getByLabel("Website zip").setInputFiles(await siteZip(`Header ${Date.now()}`, { version: 1, header: HEADER }))
    const report = page.getByTestId("website-theme")
    await expect(report).toContainText(
      "Once this is published, your portal on your own domain will use the header from theme.json.",
    )
    await expect(report.getByRole("listitem")).toHaveText([
      "header.links[1].href: Must be a page on your website, like /about.",
      "header.links[2].href: Must be a page on your website, like /about.",
      "header.links[3].label: Mixes alphabets in a way browsers warn about.",
    ])
    await publish(page)

    // 2. The landing on the practice's host, once the web app's minute-long
    // answer knows about this publish.
    await expect
      .poll(async () => (await portalHeader(onThisMachine)).wordmark, {
        message: "the portal shows the website's wordmark",
        timeout: 90_000,
        intervals: [1_000, 2_000, 5_000],
      })
      .toBe(WORDMARK)

    const watched = await onThisMachine.newPage()
    await watched.goto(`${PRIMARY_ORIGIN}/`)
    await expect(watched.getByTestId("portal-shell-no-session")).toBeVisible()
    await test.info().attach("landing with the website's header", {
      body: await watched.screenshot({ fullPage: true }),
      contentType: "image/png",
    })
    await watched.close()

    const shown = await portalHeader(onThisMachine)
    expect(shown.heading, "the practice's own name is still the heading").toBeTruthy()
    expect(shown.heading).not.toBe(WORDMARK)
    expect(shown.bannerName, "and the header's accessible name").toBe(shown.heading)
    expect(shown.wordmarkHref).toBe(`https://${SITE}/`)
    expect(shown.subtitle).toBe(SUBTITLE)
    expect(shown.websiteLinks).toEqual([{ text: "About", href: `https://${SITE}/about/` }])
    expect(shown.cta).toEqual({ text: "Schedule a visit", href: `https://${SITE}/#schedule` })

    // 3. What was left out never renders.
    for (const link of shown.links) {
      expect(link.href.toLowerCase(), "no javascript: link").not.toContain("javascript:")
      expect(link.href, "no link off the practice's hosts").not.toContain("elsewhere.example")
    }
    expect(shown.text).not.toContain("Run me")
    expect(shown.text).not.toContain("Elsewhere")
    expect(shown.text).not.toContain(MIXED_LABEL)

    // Leave the practice's site without a header for the specs after this one.
    await page.getByLabel("Website zip").setInputFiles(await siteZip(`Plain ${Date.now()}`))
    await publish(page)
  } finally {
    await onThisMachine.close()
    await practice.context.close()
  }
})
