// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Two settings changed from two tabs, each opened before the other saved,
 * both survive a reload.
 *
 * Each tab holds the preferences it loaded. A save that sent that whole copy
 * put the other tab's setting back the way it was when this tab opened, so
 * the second save silently undid the first. Saves now carry only the setting
 * they change, and the server merges it into what is stored.
 *
 * The tabs share one browser context, so one sign-in, the way two tabs of
 * one browser do. Both settings are put back afterwards: the clinician is
 * shared with every spec this worker runs next.
 */

import type { Page, Request } from "@playwright/test"
import { expect, test } from "../fixtures/auth"

const PREFERENCES = "/api/users/me/preferences"

interface Preferences {
  therapist_display_name: string | null
  calendar_density: string
}

function preferenceSave(page: Page): Promise<Request> {
  return page
    .waitForResponse(
      (response) =>
        response.url().endsWith(PREFERENCES) &&
        response.request().method() === "PATCH" &&
        response.ok(),
    )
    .then((response) => response.request())
}

test("settings saved from two tabs both survive a reload", async ({ signedInPage: page, api }) => {
  const before = await api.get<Preferences>(PREFERENCES)
  const newDensity = before.calendar_density === "compact" ? "Gentle" : "Compact"
  const newName = `Dr. Two Tabs ${Date.now()}`

  try {
    // Both tabs load their copy before either saves.
    await page.goto("/dashboard/settings/profile")
    const nameField = page.getByLabel("Display Name")
    await expect(nameField).toBeVisible()

    const appearance = await page.context().newPage()
    await appearance.goto("/dashboard/settings/appearance")
    const densities = appearance.getByRole("radiogroup", { name: "Calendar density" })
    await expect(densities).toBeVisible()

    // First tab: the display name.
    await nameField.fill(newName)
    const nameSaved = preferenceSave(page)
    await page.getByRole("button", { name: "Save" }).first().click()
    expect((await nameSaved).postDataJSON()).toEqual({ therapist_display_name: newName })

    // Second tab, still holding the copy from before the name was saved.
    const densitySaved = preferenceSave(appearance)
    await densities.getByRole("radio", { name: new RegExp(newDensity) }).click()
    expect((await densitySaved).postDataJSON()).toEqual({
      calendar_density: newDensity.toLowerCase(),
    })

    // Both, after a reload, in both tabs.
    await page.reload()
    await expect(page.getByLabel("Display Name")).toHaveValue(newName)
    await appearance.reload()
    await expect(
      appearance
        .getByRole("radiogroup", { name: "Calendar density" })
        .getByRole("radio", { name: new RegExp(newDensity) }),
    ).toHaveAttribute("aria-checked", "true")

    const after = await api.get<Preferences>(PREFERENCES)
    expect(after.therapist_display_name).toBe(newName)
    expect(after.calendar_density).toBe(newDensity.toLowerCase())
    await appearance.close()
  } finally {
    await api.patch(PREFERENCES, {
      therapist_display_name: before.therapist_display_name,
      calendar_density: before.calendar_density,
    })
  }
})
