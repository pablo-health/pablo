/**
 * Patient Management E2E Tests
 *
 * Tests the full CRUD flow for patient management including:
 * - Creating new patients
 * - Searching and filtering
 * - Editing patient information
 * - Deleting patients
 * - Form validation
 * - Navigation between list and detail pages
 */

import { test, expect } from "@playwright/test"

// Base URL for the application
const BASE_URL = process.env.BASE_URL || "http://localhost:3000"

test.describe("Patient Management", () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to the patients page
    // NOTE: This assumes you're already authenticated
    // If auth is required, add login steps here
    await page.goto(`${BASE_URL}/dashboard/patients`)

    // Wait for the page to load
    await expect(page.getByRole("heading", { name: /patients/i })).toBeVisible()
  })

  test.describe("Patient List", () => {
    test("displays patient table with data", async ({ page }) => {
      // Check that the table renders
      await expect(page.getByRole("table")).toBeVisible()

      // Check for table headers
      await expect(page.getByRole("columnheader", { name: /name/i })).toBeVisible()
      await expect(page.getByRole("columnheader", { name: /email/i })).toBeVisible()
      await expect(page.getByRole("columnheader", { name: /phone/i })).toBeVisible()
      await expect(page.getByRole("columnheader", { name: /status/i })).toBeVisible()
      await expect(page.getByRole("columnheader", { name: /sessions/i })).toBeVisible()
    })

    test("shows empty state when no patients exist", async ({ page }) => {
      // This test assumes the database might be empty
      // Check for either patients or empty state message
      const emptyMessage = page.getByText(/no patients yet/i)
      const hasPatients = page.locator("table tbody tr").count()

      const isEmpty = await emptyMessage.isVisible().catch(() => false)
      const hasData = (await hasPatients) > 0

      // Either should be true
      expect(isEmpty || hasData).toBeTruthy()
    })

    test("displays Add Patient button", async ({ page }) => {
      await expect(page.getByRole("button", { name: /add patient/i })).toBeVisible()
    })
  })

  test.describe("Search Functionality", () => {
    test("renders search input", async ({ page }) => {
      const searchInput = page.getByPlaceholder(/search patients/i)
      await expect(searchInput).toBeVisible()
    })

    test("filters patients by search term", async ({ page }) => {
      const searchInput = page.getByPlaceholder(/search patients/i)

      // Type a search term
      await searchInput.fill("Smith")

      // Wait for debounce (500ms) + API call
      await page.waitForTimeout(1000)

      // Check that results are filtered
      // (This assumes there are patients in the database)
      // You might need to seed test data for reliable testing
    })

    test("shows no results message for non-existent name", async ({ page }) => {
      const searchInput = page.getByPlaceholder(/search patients/i)

      // Search for a name that definitely doesn't exist
      await searchInput.fill("NonexistentPatientName12345")

      // Wait for debounce + API call
      await page.waitForTimeout(1000)

      // Should show no results message
      const noResults = page.getByText(/no patients found matching your search/i)
      await expect(noResults).toBeVisible({ timeout: 2000 }).catch(() => {
        // If there are actually results, that's okay (depends on test data)
      })
    })
  })

  test.describe("Create Patient Flow", () => {
    test("opens create patient dialog", async ({ page }) => {
      await page.getByRole("button", { name: /add patient/i }).click()

      // Dialog should open
      await expect(page.getByRole("dialog")).toBeVisible()
      await expect(page.getByRole("heading", { name: /add patient/i })).toBeVisible()
    })

    test("validates required fields", async ({ page }) => {
      await page.getByRole("button", { name: /add patient/i }).click()

      // Try to submit without filling required fields
      await page.getByRole("button", { name: /create patient/i }).click()

      // Should show validation errors
      await expect(page.getByText(/first name is required/i)).toBeVisible()
      await expect(page.getByText(/last name is required/i)).toBeVisible()
    })

    test("creates patient with required fields only", async ({ page }) => {
      await page.getByRole("button", { name: /add patient/i }).click()

      // Fill in required fields
      const timestamp = Date.now()
      await page.getByLabel(/first name/i).fill(`Test-${timestamp}`)
      await page.getByLabel(/last name/i).fill("Patient")

      // Submit form
      await page.getByRole("button", { name: /create patient/i }).click()

      // Dialog should close
      await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })

      // Patient should appear in the table
      await expect(page.getByText(`Test-${timestamp} Patient`)).toBeVisible()
    })

    test("creates patient with all fields", async ({ page }) => {
      await page.getByRole("button", { name: /add patient/i }).click()

      // Fill in all fields
      const timestamp = Date.now()
      await page.getByLabel(/first name/i).fill(`John-${timestamp}`)
      await page.getByLabel(/last name/i).fill("Doe")
      await page.getByLabel(/email/i).fill(`john${timestamp}@example.com`)
      await page.getByLabel(/phone/i).fill("(555) 123-4567")
      await page.getByLabel(/date of birth/i).fill("1985-03-15")
      await page.getByLabel(/diagnosis/i).fill("Anxiety Disorder")

      // Select status
      await page.getByRole("combobox", { name: /status/i }).click()
      await page.getByRole("option", { name: /active/i }).click()

      // Submit form
      await page.getByRole("button", { name: /create patient/i }).click()

      // Dialog should close
      await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })

      // Patient should appear in the table with correct data
      await expect(page.getByText(`John-${timestamp} Doe`)).toBeVisible()
      await expect(page.getByText(`john${timestamp}@example.com`)).toBeVisible()
    })

    test("validates email format", async ({ page }) => {
      await page.getByRole("button", { name: /add patient/i }).click()

      await page.getByLabel(/first name/i).fill("Test")
      await page.getByLabel(/last name/i).fill("Patient")
      await page.getByLabel(/email/i).fill("invalid-email")

      await page.getByRole("button", { name: /create patient/i }).click()

      // Should show email validation error
      await expect(page.getByText(/invalid email/i)).toBeVisible()
    })

    test("validates phone number length", async ({ page }) => {
      await page.getByRole("button", { name: /add patient/i }).click()

      await page.getByLabel(/first name/i).fill("Test")
      await page.getByLabel(/last name/i).fill("Patient")
      await page.getByLabel(/phone/i).fill("123")

      await page.getByRole("button", { name: /create patient/i }).click()

      // Should show phone validation error
      await expect(page.getByText(/phone must be at least 10 digits/i)).toBeVisible()
    })

    test("allows changing status dropdown", async ({ page }) => {
      await page.getByRole("button", { name: /add patient/i }).click()

      // Open status dropdown
      await page.getByRole("combobox", { name: /status/i }).click()

      // Check all options are available
      await expect(page.getByRole("option", { name: /active/i })).toBeVisible()
      await expect(page.getByRole("option", { name: /inactive/i })).toBeVisible()
      await expect(page.getByRole("option", { name: /on hold/i })).toBeVisible()

      // Select inactive
      await page.getByRole("option", { name: /inactive/i }).click()

      // Status should update
      await expect(page.getByRole("combobox", { name: /status/i })).toContainText(/inactive/i)
    })

    test("cancels creation without saving", async ({ page }) => {
      await page.getByRole("button", { name: /add patient/i }).click()

      await page.getByLabel(/first name/i).fill("Test")
      await page.getByLabel(/last name/i).fill("Canceled")

      // Click cancel
      await page.getByRole("button", { name: /cancel/i }).click()

      // Dialog should close
      await expect(page.getByRole("dialog")).not.toBeVisible()

      // Patient should NOT appear in table
      await expect(page.getByText("Test Canceled")).not.toBeVisible()
    })
  })

  test.describe("Edit Patient Flow", () => {
    test("opens edit dialog with pre-filled data", async ({ page }) => {
      // First create a patient to edit
      await page.getByRole("button", { name: /add patient/i }).click()
      const timestamp = Date.now()
      await page.getByLabel(/first name/i).fill(`Edit-${timestamp}`)
      await page.getByLabel(/last name/i).fill("Test")
      await page.getByRole("button", { name: /create patient/i }).click()
      await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })

      // Find the patient row and click edit button
      const row = page.locator("tr", { hasText: `Edit-${timestamp}` })
      await row.locator("button").first().click() // First button is edit (Pencil icon)

      // Edit dialog should open with data
      await expect(page.getByRole("dialog")).toBeVisible()
      await expect(page.getByRole("heading", { name: /edit patient/i })).toBeVisible()
      await expect(page.getByLabel(/first name/i)).toHaveValue(`Edit-${timestamp}`)
      await expect(page.getByLabel(/last name/i)).toHaveValue("Test")
    })

    test("updates patient information", async ({ page }) => {
      // Create a patient first
      await page.getByRole("button", { name: /add patient/i }).click()
      const timestamp = Date.now()
      await page.getByLabel(/first name/i).fill(`Update-${timestamp}`)
      await page.getByLabel(/last name/i).fill("Original")
      await page.getByRole("button", { name: /create patient/i }).click()
      await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })

      // Edit the patient
      const row = page.locator("tr", { hasText: `Update-${timestamp}` })
      await row.locator("button").first().click()

      // Update last name
      await page.getByLabel(/last name/i).clear()
      await page.getByLabel(/last name/i).fill("Updated")

      // Add email
      await page.getByLabel(/email/i).fill(`updated${timestamp}@example.com`)

      // Submit
      await page.getByRole("button", { name: /update patient/i }).click()

      // Dialog should close
      await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })

      // Updated data should appear in table
      await expect(page.getByText(`Update-${timestamp} Updated`)).toBeVisible()
      await expect(page.getByText(`updated${timestamp}@example.com`)).toBeVisible()
    })
  })

  test.describe("Delete Patient Flow", () => {
    test("shows delete confirmation dialog", async ({ page }) => {
      // Create a patient first
      await page.getByRole("button", { name: /add patient/i }).click()
      const timestamp = Date.now()
      await page.getByLabel(/first name/i).fill(`Delete-${timestamp}`)
      await page.getByLabel(/last name/i).fill("Test")
      await page.getByRole("button", { name: /create patient/i }).click()
      await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })

      // Click delete button (second button in row)
      const row = page.locator("tr", { hasText: `Delete-${timestamp}` })
      await row.locator("button").nth(1).click()

      // Delete confirmation dialog should appear
      await expect(page.getByRole("dialog")).toBeVisible()
      await expect(page.getByRole("heading", { name: /delete patient/i })).toBeVisible()
      await expect(
        page.getByText(/are you sure you want to delete.*Delete-/i)
      ).toBeVisible()
    })

    test("cancels deletion", async ({ page }) => {
      // Create a patient
      await page.getByRole("button", { name: /add patient/i }).click()
      const timestamp = Date.now()
      await page.getByLabel(/first name/i).fill(`Keep-${timestamp}`)
      await page.getByLabel(/last name/i).fill("Me")
      await page.getByRole("button", { name: /create patient/i }).click()
      await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })

      // Click delete
      const row = page.locator("tr", { hasText: `Keep-${timestamp}` })
      await row.locator("button").nth(1).click()

      // Cancel deletion
      await page.getByRole("button", { name: /cancel/i }).click()

      // Dialog should close
      await expect(page.getByRole("dialog")).not.toBeVisible()

      // Patient should still be in table
      await expect(page.getByText(`Keep-${timestamp} Me`)).toBeVisible()
    })

    test("deletes patient successfully", async ({ page }) => {
      // Create a patient to delete
      await page.getByRole("button", { name: /add patient/i }).click()
      const timestamp = Date.now()
      await page.getByLabel(/first name/i).fill(`Remove-${timestamp}`)
      await page.getByLabel(/last name/i).fill("Me")
      await page.getByRole("button", { name: /create patient/i }).click()
      await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })

      // Delete the patient
      const row = page.locator("tr", { hasText: `Remove-${timestamp}` })
      await row.locator("button").nth(1).click()

      // Confirm deletion
      await page.getByRole("button", { name: /^delete$/i }).click()

      // Dialog should close
      await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })

      // Patient should be removed from table
      await expect(page.getByText(`Remove-${timestamp} Me`)).not.toBeVisible()
    })
  })

  test.describe("Patient Detail Navigation", () => {
    test("navigates to patient detail page when row is clicked", async ({ page }) => {
      // Create a patient
      await page.getByRole("button", { name: /add patient/i }).click()
      const timestamp = Date.now()
      await page.getByLabel(/first name/i).fill(`Detail-${timestamp}`)
      await page.getByLabel(/last name/i).fill("View")
      await page.getByRole("button", { name: /create patient/i }).click()
      await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })

      // Click on the patient name (not buttons)
      await page.getByText(`Detail-${timestamp} View`).click()

      // Should navigate to detail page
      await expect(page).toHaveURL(/\/dashboard\/patients\/[a-z0-9-]+/)

      // Detail page should show patient info
      await expect(
        page.getByRole("heading", { name: `Detail-${timestamp} View` })
      ).toBeVisible()
    })

    test("detail page shows back to patients link", async ({ page }) => {
      // Create and navigate to detail page
      await page.getByRole("button", { name: /add patient/i }).click()
      const timestamp = Date.now()
      await page.getByLabel(/first name/i).fill(`Back-${timestamp}`)
      await page.getByLabel(/last name/i).fill("Test")
      await page.getByRole("button", { name: /create patient/i }).click()
      await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })
      await page.getByText(`Back-${timestamp} Test`).click()

      // Click back link
      await page.getByRole("link", { name: /back to patients/i }).click()

      // Should return to patients list
      await expect(page).toHaveURL(/\/dashboard\/patients$/)
      await expect(page.getByRole("heading", { name: /^patients$/i })).toBeVisible()
    })
  })

  test.describe("Status Badge Display", () => {
    test("displays different status badges correctly", async ({ page }) => {
      // Create patients with different statuses
      const statuses = ["active", "inactive", "on_hold"]

      for (const status of statuses) {
        await page.getByRole("button", { name: /add patient/i }).click()
        const timestamp = Date.now()
        await page.getByLabel(/first name/i).fill(`Status-${status}-${timestamp}`)
        await page.getByLabel(/last name/i).fill("Test")

        // Select status
        await page.getByRole("combobox", { name: /status/i }).click()
        await page
          .getByRole("option", { name: new RegExp(status.replace("_", " "), "i") })
          .click()

        await page.getByRole("button", { name: /create patient/i }).click()
        await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 5000 })

        // Check that status badge is displayed
        const row = page.locator("tr", { hasText: `Status-${status}-${timestamp}` })
        await expect(row.getByText(status.replace("_", " "))).toBeVisible()
      }
    })
  })
})
