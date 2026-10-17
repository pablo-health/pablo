// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings > Practice > Compliance, end to end: add a reminder from the
 * settings page, find it on the dashboard's compliance panel, follow a link
 * that opens the settings page on that one item, then delete it and see it
 * leave the dashboard.
 *
 * The two surfaces render the same panel over the same items. What this
 * proves that a unit test cannot is that they are the same items: a page that
 * kept its own copy, or a dashboard that cached a stale list, would pass every
 * component test and fail here.
 */

import { test, expect } from "../fixtures/auth"

type ComplianceItem = { id: string; label: string; due_date: string | null }

/** Ten days out: inside the dashboard's default "next 30 days" view. */
function tenDaysFromNow(): string {
  const day = new Date()
  day.setDate(day.getDate() + 10)
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`
}

test("a reminder added in Settings > Compliance shows on the dashboard and goes when deleted", async ({
  signedInPage: page,
  api,
}) => {
  const label = `Board renewal ${Date.now()}`
  let itemId: string | null = null
  try {
    await page.goto("/dashboard/settings/compliance")
    await expect(page.getByRole("heading", { level: 1, name: "Compliance" })).toBeVisible()

    await page.getByRole("button", { name: "Add reminder" }).click()
    await page.getByRole("button", { name: /Custom reminder/ }).click()
    await page.getByLabel("Label").fill(label)
    await page.locator("#composer-due").fill(tenDaysFromNow())

    const created = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/compliance" && r.request().method() === "POST",
    )
    await page.getByRole("button", { name: "Save reminder" }).click()
    const createdResponse = await created
    expect(createdResponse.status(), await createdResponse.text()).toBe(201)
    itemId = ((await createdResponse.json()) as ComplianceItem).id

    // On the settings page itself, in the default view.
    await expect(page.getByTestId(`compliance-item-${itemId}`)).toContainText(label)

    // The dashboard's panel reads the same items.
    await page.goto("/dashboard")
    await expect(page.getByTestId(`compliance-item-${itemId}`)).toContainText(label)

    // A link to the item opens the settings page on it, expanded.
    await page.goto(`/dashboard/settings/compliance?item=${itemId}`)
    const linked = page.getByTestId(`compliance-item-${itemId}`)
    await expect(linked).toHaveAttribute("aria-current", "true")
    await expect(linked.getByTestId("compliance-item-details")).toBeVisible()

    await api.request("DELETE", `/api/compliance/${itemId}`)
    itemId = null
    const remaining = await api.get<ComplianceItem[]>("/api/compliance")
    expect(remaining.map((item) => item.label)).not.toContain(label)

    await page.goto("/dashboard")
    await expect(page.getByRole("heading", { name: "Compliance" })).toBeVisible()
    await expect(page.getByText(label)).toHaveCount(0)
  } finally {
    if (itemId) await api.request("DELETE", `/api/compliance/${itemId}`).catch(() => undefined)
  }
})
