// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient management, end to end: the list, search, create / edit / delete
 * through the dialogs, form validation, and navigation to the detail page.
 * Runs as a signed-in clinician against the compose stack.
 */

import type { Page } from "@playwright/test"
import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

async function openPatients(page: Page): Promise<void> {
  await page.goto("/dashboard/patients")
  await expect(page.getByRole("heading", { name: /patients/i })).toBeVisible()
}

async function createPatientViaDialog(page: Page, firstName: string, lastName: string): Promise<void> {
  await page.getByRole("button", { name: /add patient/i }).click()
  await page.getByLabel(/first name/i).fill(firstName)
  await page.getByLabel(/last name/i).fill(lastName)
  await page.getByRole("button", { name: /create patient/i }).click()
  await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })
}

test.describe("Patient Management", () => {
  test.beforeEach(async ({ signedInPage }) => {
    await openPatients(signedInPage)
  })

  test.describe("Patient List", () => {
    test("displays patient table with data", async ({ signedInPage: page, api }) => {
      await givePatient(api)
      await openPatients(page)

      await expect(page.getByRole("table")).toBeVisible()
      await expect(page.getByRole("columnheader", { name: /name/i })).toBeVisible()
      await expect(page.getByRole("columnheader", { name: /email/i })).toBeVisible()
      await expect(page.getByRole("columnheader", { name: /phone/i })).toBeVisible()
      await expect(page.getByRole("columnheader", { name: /status/i })).toBeVisible()
      await expect(page.getByRole("columnheader", { name: /sessions/i })).toBeVisible()
    })

    test("shows empty state or patients", async ({ signedInPage: page }) => {
      const emptyMessage = page.getByText(/no patients yet/i)
      const rows = page.locator("table tbody tr")

      const isEmpty = await emptyMessage.isVisible().catch(() => false)
      const hasData = (await rows.count()) > 0

      expect(isEmpty || hasData).toBeTruthy()
    })

    test("displays Add Patient button", async ({ signedInPage: page }) => {
      await expect(page.getByRole("button", { name: /add patient/i })).toBeVisible()
    })
  })

  test.describe("Search Functionality", () => {
    test("renders search input", async ({ signedInPage: page }) => {
      await expect(page.getByPlaceholder(/search patients/i)).toBeVisible()
    })

    test("filters patients by search term", async ({ signedInPage: page, api }) => {
      const smith = await givePatient(api, { last_name: "Smith" })
      const other = await givePatient(api, { last_name: "Jones" })
      await openPatients(page)

      await page.getByPlaceholder(/search patients/i).fill("Smith")

      await expect(page.getByText(`${smith.first_name} Smith`)).toBeVisible()
      await expect(page.getByText(`${other.first_name} Jones`)).not.toBeVisible()
    })

    test("shows no results message for non-existent name", async ({ signedInPage: page }) => {
      await page.getByPlaceholder(/search patients/i).fill("NonexistentPatientName12345")

      await expect(page.getByText(/no patients found matching your search/i)).toBeVisible()
    })
  })

  test.describe("Create Patient Flow", () => {
    test("opens create patient dialog", async ({ signedInPage: page }) => {
      await page.getByRole("button", { name: /add patient/i }).click()

      await expect(page.getByRole("dialog")).toBeVisible()
      await expect(page.getByRole("heading", { name: /add patient/i })).toBeVisible()
    })

    test("validates required fields", async ({ signedInPage: page }) => {
      await page.getByRole("button", { name: /add patient/i }).click()
      await page.getByRole("button", { name: /create patient/i }).click()

      await expect(page.getByText(/first name is required/i)).toBeVisible()
      await expect(page.getByText(/last name is required/i)).toBeVisible()
    })

    test("creates patient with required fields only", async ({ signedInPage: page }) => {
      const timestamp = Date.now()
      await createPatientViaDialog(page, `Test-${timestamp}`, "Patient")

      await expect(page.getByText(`Test-${timestamp} Patient`)).toBeVisible()
    })

    test("creates patient with all fields", async ({ signedInPage: page }) => {
      await page.getByRole("button", { name: /add patient/i }).click()

      const timestamp = Date.now()
      await page.getByLabel(/first name/i).fill(`John-${timestamp}`)
      await page.getByLabel(/last name/i).fill("Doe")
      await page.getByLabel(/email/i).fill(`john${timestamp}@example.com`)
      await page.getByLabel(/phone/i).fill("(555) 123-4567")
      await page.getByLabel(/date of birth/i).fill("1985-03-15")
      await page.getByLabel(/diagnosis/i).fill("Anxiety Disorder")

      await page.getByRole("combobox", { name: /status/i }).click()
      await page.getByRole("option", { name: "Active", exact: true }).click()

      await page.getByRole("button", { name: /create patient/i }).click()
      await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })

      await expect(page.getByText(`John-${timestamp} Doe`)).toBeVisible()
      await expect(page.getByText(`john${timestamp}@example.com`)).toBeVisible()
    })

    test("validates email format", async ({ signedInPage: page }) => {
      await page.getByRole("button", { name: /add patient/i }).click()

      await page.getByLabel(/first name/i).fill("Test")
      await page.getByLabel(/last name/i).fill("Patient")
      const emailInput = page.getByLabel(/email/i)
      await emailInput.fill("invalid-email")

      await page.getByRole("button", { name: /create patient/i }).click()

      // The field is type="email": the browser refuses the submit and reports
      // the format problem itself, before the form's own validator runs.
      expect(await emailInput.evaluate((el: HTMLInputElement) => el.validity.valid)).toBe(false)
      await expect(page.getByRole("dialog")).toBeVisible()
    })

    test("validates phone number length", async ({ signedInPage: page }) => {
      await page.getByRole("button", { name: /add patient/i }).click()

      await page.getByLabel(/first name/i).fill("Test")
      await page.getByLabel(/last name/i).fill("Patient")
      await page.getByLabel(/phone/i).fill("123")

      await page.getByRole("button", { name: /create patient/i }).click()

      await expect(page.getByText(/phone must be at least 10 digits/i)).toBeVisible()
    })

    test("allows changing status dropdown", async ({ signedInPage: page }) => {
      await page.getByRole("button", { name: /add patient/i }).click()

      await page.getByRole("combobox", { name: /status/i }).click()

      await expect(page.getByRole("option", { name: "Active", exact: true })).toBeVisible()
      await expect(page.getByRole("option", { name: "Inactive", exact: true })).toBeVisible()
      await expect(page.getByRole("option", { name: "On Hold", exact: true })).toBeVisible()

      await page.getByRole("option", { name: /inactive/i }).click()

      await expect(page.getByRole("combobox", { name: /status/i })).toContainText(/inactive/i)
    })

    test("cancels creation without saving", async ({ signedInPage: page }) => {
      await page.getByRole("button", { name: /add patient/i }).click()

      await page.getByLabel(/first name/i).fill("Test")
      await page.getByLabel(/last name/i).fill("Canceled")

      await page.getByRole("button", { name: /cancel/i }).click()

      await expect(page.getByRole("dialog")).not.toBeVisible()
      await expect(page.getByText("Test Canceled")).not.toBeVisible()
    })
  })

  test.describe("Edit Patient Flow", () => {
    test("opens edit dialog with pre-filled data", async ({ signedInPage: page }) => {
      const timestamp = Date.now()
      await createPatientViaDialog(page, `Edit-${timestamp}`, "Test")

      const row = page.locator("tr", { hasText: `Edit-${timestamp}` })
      await row.locator("button").first().click()

      await expect(page.getByRole("dialog")).toBeVisible()
      await expect(page.getByRole("heading", { name: /edit patient/i })).toBeVisible()
      await expect(page.getByLabel(/first name/i)).toHaveValue(`Edit-${timestamp}`)
      await expect(page.getByLabel(/last name/i)).toHaveValue("Test")
    })

    test("updates patient information", async ({ signedInPage: page }) => {
      const timestamp = Date.now()
      await createPatientViaDialog(page, `Update-${timestamp}`, "Original")

      const row = page.locator("tr", { hasText: `Update-${timestamp}` })
      await row.locator("button").first().click()

      await page.getByLabel(/last name/i).clear()
      await page.getByLabel(/last name/i).fill("Updated")
      await page.getByLabel(/email/i).fill(`updated${timestamp}@example.com`)

      await page.getByRole("button", { name: /update patient/i }).click()
      await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })

      await expect(page.getByText(`Update-${timestamp} Updated`)).toBeVisible()
      await expect(page.getByText(`updated${timestamp}@example.com`)).toBeVisible()
    })
  })

  test.describe("Delete Patient Flow", () => {
    test("shows delete confirmation dialog", async ({ signedInPage: page }) => {
      const timestamp = Date.now()
      await createPatientViaDialog(page, `Delete-${timestamp}`, "Test")

      const row = page.locator("tr", { hasText: `Delete-${timestamp}` })
      await row.locator("button").nth(1).click()

      await expect(page.getByRole("dialog")).toBeVisible()
      await expect(page.getByRole("heading", { name: /delete patient/i })).toBeVisible()
      await expect(page.getByText(/are you sure you want to delete.*Delete-/i)).toBeVisible()
    })

    test("cancels deletion", async ({ signedInPage: page }) => {
      const timestamp = Date.now()
      await createPatientViaDialog(page, `Keep-${timestamp}`, "Me")

      const row = page.locator("tr", { hasText: `Keep-${timestamp}` })
      await row.locator("button").nth(1).click()

      await page.getByRole("button", { name: /cancel/i }).click()

      await expect(page.getByRole("dialog")).not.toBeVisible()
      await expect(page.getByText(`Keep-${timestamp} Me`)).toBeVisible()
    })

    test("deletes patient successfully", async ({ signedInPage: page }) => {
      const timestamp = Date.now()
      await createPatientViaDialog(page, `Remove-${timestamp}`, "Me")

      const row = page.locator("tr", { hasText: `Remove-${timestamp}` })
      await row.locator("button").nth(1).click()

      // Deleting a record is gated on the clinician confirming the retention
      // obligation has been met; the Delete button stays disabled until then.
      await page.getByRole("checkbox", { name: /retention obligations/i }).check()
      await page.getByRole("button", { name: /^delete$/i }).click()
      await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })

      await expect(page.getByText(`Remove-${timestamp} Me`)).not.toBeVisible()
    })
  })

  test.describe("Patient Detail Navigation", () => {
    test("navigates to patient detail page when row is clicked", async ({ signedInPage: page }) => {
      const timestamp = Date.now()
      await createPatientViaDialog(page, `Detail-${timestamp}`, "View")

      await page.getByText(`Detail-${timestamp} View`).click()

      await expect(page).toHaveURL(/\/dashboard\/patients\/[a-z0-9-]+/)
      await expect(page.getByRole("heading", { name: `Detail-${timestamp} View` })).toBeVisible()
    })

    test("detail page shows back to patients link", async ({ signedInPage: page }) => {
      const timestamp = Date.now()
      await createPatientViaDialog(page, `Back-${timestamp}`, "Test")
      await page.getByText(`Back-${timestamp} Test`).click()

      await page.getByRole("link", { name: /back to patients/i }).click()

      await expect(page).toHaveURL(/\/dashboard\/patients$/)
      await expect(page.getByRole("heading", { name: /^patients$/i })).toBeVisible()
    })
  })

  test.describe("Status Badge Display", () => {
    test("displays different status badges correctly", async ({ signedInPage: page }) => {
      const statuses = { active: "Active", inactive: "Inactive", on_hold: "On Hold" }

      for (const [status, label] of Object.entries(statuses)) {
        await page.getByRole("button", { name: /add patient/i }).click()
        const timestamp = Date.now()
        await page.getByLabel(/first name/i).fill(`Status-${status}-${timestamp}`)
        await page.getByLabel(/last name/i).fill("Test")

        await page.getByRole("combobox", { name: /status/i }).click()
        await page.getByRole("option", { name: label, exact: true }).click()

        await page.getByRole("button", { name: /create patient/i }).click()
        await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })

        const row = page.locator("tr", { hasText: `Status-${status}-${timestamp}` })
        await expect(row.getByText(status.replace("_", " "), { exact: true })).toBeVisible()
      }
    })
  })
})
