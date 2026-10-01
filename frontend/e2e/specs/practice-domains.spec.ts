// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice owner manages the practice's own domains from Settings.
 *
 * What only the real stack can prove: the page adds hosts through the real
 * route into the real table (a website bringing its www alias), shows the DNS
 * record this deployment is configured to name, survives a reload, moves the
 * primary between two working hosts — the partial unique index allowing it —
 * and removes hosts again. And that "Check now" asks DNS — the stack's stand-in
 * name server (scripts/fake_dns.py), filled from here — and reports each record
 * found only once it is published, without moving the host's status.
 *
 * The two working hosts are written by backend/scripts/e2e_seed_practice_domains.py,
 * for a practice of their own: nothing in the product marks a host working on
 * a practice's say-so, so a browser cannot make one. What those hosts serve is
 * practice-own-host.spec.ts.
 *
 * Deliberately not here: hostname validation, another practice's host, the
 * non-owner refusal and the audit rows, which cost milliseconds in
 * backend/tests/test_practice_domains_routes.py.
 */

import type { Page } from "@playwright/test"
import { test as base, expect } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { dns } from "../fixtures/dns"
import { type FreshPractice, signInToFreshPractice } from "../fixtures/freshPractice"

const SETTINGS_PATH = "/dashboard/settings/domains"
const CNAME_TARGET = "sites.e2e-stack.example"
const WORKING = ["portal.e2e-practice.example", "clients.e2e-practice.example"] as const
const ADDED_SUFFIX = ".e2e-added.example"

interface Domain {
  domain: string
  purpose: "portal" | "site"
  status: string
  is_primary: boolean
  dns_records: { type: string; name: string; value: string }[]
  apex_verified_at: string | null
}

async function domains(api: ApiClient): Promise<Domain[]> {
  return (await api.get<{ domains: Domain[] }>("/api/practice/domains")).domains
}

/** Hosts this spec added on an earlier, interrupted run. */
async function removeAddedHosts(api: ApiClient): Promise<void> {
  for (const d of await domains(api)) {
    if (d.domain.endsWith(ADDED_SUFFIX)) {
      await api.delete(`/api/practice/domains/${encodeURIComponent(d.domain)}`)
    }
  }
}

function row(page: Page, host: string) {
  return page.getByTestId(`domain-row-${host}`)
}

/**
 * Signed in as the owner of the practice these hosts belong to — a practice of
 * its own, so moving its primary never moves the shared practice's portal
 * links (see fixtures/freshPractice.ts). Hosts an interrupted earlier run
 * added are removed before and after.
 */
const test = base.extend<{ practice: FreshPractice }>({
  practice: async ({ browser }, provide) => {
    const practice = await signInToFreshPractice(browser, "domains")
    await removeAddedHosts(practice.api)
    try {
      await provide(practice)
    } finally {
      await removeAddedHosts(practice.api)
      await practice.context.close()
    }
  },
})

test.describe("A practice's own domains", () => {
  test("the owner adds a website with its www alias, sees the record, and removes both", async ({
    practice: { api, page },
  }) => {
    const host = `site${Date.now()}${ADDED_SUFFIX}`
    const www = `www.${host}`

    await page.goto(SETTINGS_PATH)
    await page.getByLabel("Domain", { exact: true }).fill(host)
    await page.getByRole("radio", { name: "Website" }).click()
    // Three labels, so the alias is offered but not ticked for us.
    const alias = page.getByRole("checkbox", { name: `Also add ${www}` })
    await expect(alias).not.toBeChecked()
    await alias.check()
    await page.getByRole("button", { name: "Add domain" }).click()

    for (const added of [host, www]) {
      await expect(row(page, added)).toContainText("Waiting for DNS")
      await expect(row(page, added).getByRole("button", { name: "Make primary" })).toHaveCount(0)
      const records = row(page, added).getByRole("table", { name: `DNS records for ${added}` })
      await expect(records).toContainText("CNAME")
      await expect(records).toContainText(CNAME_TARGET)
    }

    await page.reload()
    await expect(row(page, host)).toBeVisible()
    await expect(row(page, www)).toBeVisible()
    const stored = (await domains(api)).filter((d) => d.domain === host || d.domain === www)
    expect(stored.map((d) => [d.domain, d.purpose, d.status, d.is_primary]).sort()).toEqual(
      [
        [host, "site", "pending", false],
        [www, "site", "pending", false],
      ].sort(),
    )

    for (const removed of [host, www]) {
      await row(page, removed).getByRole("button", { name: "Remove" }).click()
      await expect(row(page, removed)).toContainText(`Remove ${removed}?`)
      await row(page, removed).getByRole("button", { name: "Remove" }).click()
      await expect(row(page, removed)).toHaveCount(0)
    }
    const left = (await domains(api)).map((d) => d.domain)
    expect(left).not.toContain(host)
    expect(left).not.toContain(www)
  })

  test("the owner checks a new address's records and sees each one found once it is published", async ({
    practice: { api, page },
  }) => {
    const host = `portal${Date.now()}${ADDED_SUFFIX}`
    const verifyName = `_pablo-verify${ADDED_SUFFIX}`

    await page.goto(SETTINGS_PATH)
    await page.getByLabel("Domain", { exact: true }).fill(host)
    await page.getByRole("button", { name: "Add domain" }).click()

    // The domain's ownership record is shown beside the host's own.
    const records = row(page, host).getByRole("table", { name: `DNS records for ${host}` })
    await expect(records).toContainText(verifyName)
    const verify = (await domains(api))
      .find((d) => d.domain === host)
      ?.dns_records.find((r) => r.type === "TXT" && r.name === verifyName)
    expect(verify?.value).toMatch(/^pablo-verify=[\w-]{32}$/)
    await expect(records).toContainText(verify!.value)

    try {
      await page.getByRole("button", { name: "Check now" }).click()
      await expect(row(page, host).getByTestId(`check-CNAME-${host}`)).toHaveText("Not found yet")
      await expect(row(page, host).getByTestId(`check-TXT-${verifyName}`)).toHaveText("Not found yet")

      await dns.set(host, "CNAME", [CNAME_TARGET])
      await dns.set(verifyName, "TXT", [verify!.value])
      await page.getByRole("button", { name: "Check now" }).click()
      await expect(row(page, host).getByTestId(`check-CNAME-${host}`)).toHaveText("Found")
      await expect(row(page, host).getByTestId(`check-TXT-${verifyName}`)).toHaveText("Found")

      // Records in place are not the address working: its status is unchanged.
      await expect(row(page, host)).toContainText("Waiting for DNS")
      const stored = (await domains(api)).find((d) => d.domain === host)
      expect(stored?.status).toBe("pending")
      expect(stored?.apex_verified_at).not.toBeNull()
    } finally {
      await dns.set(host, "CNAME", [])
      await dns.set(verifyName, "TXT", [])
    }
  })

  test("the owner moves the primary portal address between two that work", async ({
    practice: { api, page },
  }) => {
    await page.goto(SETTINGS_PATH)
    for (const host of WORKING) {
      await expect(row(page, host)).toContainText("Active")
    }

    // Whichever is not primary now becomes it, then the other takes over.
    const current = (await domains(api)).find((d) => d.is_primary && d.purpose === "portal")
    const first = current?.domain === WORKING[0] ? WORKING[1] : WORKING[0]
    const second = first === WORKING[0] ? WORKING[1] : WORKING[0]

    for (const chosen of [first, second]) {
      const other = chosen === first ? second : first
      await row(page, chosen).getByRole("button", { name: "Make primary" }).click()
      await expect(row(page, chosen)).toContainText("Primary")
      await expect(row(page, other).getByRole("button", { name: "Make primary" })).toBeVisible()

      const primaries = (await domains(api)).filter((d) => d.purpose === "portal" && d.is_primary)
      expect(primaries.map((d) => d.domain)).toEqual([chosen])
    }
  })
})
