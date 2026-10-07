// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A form with sections, walked as parts.
 *
 * The unit tests pin the walk against fixtures. This spec is for the part
 * they cannot reach: that a form published through the builder, with its
 * sections stored and served by the real API, reaches the patient as named
 * parts — a section is never a screen of its own, the count stays inside
 * the part, the review screen groups answers by part, and the server takes
 * the form in at the end.
 */

import { expect, test } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { givePortalInvitation, openPortalSection, signInToPortal } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"

interface IntakeVersion {
  id: string
  published_at: string | null
}

interface IntakeTemplate {
  id: string
  versions: IntakeVersion[]
}

interface Assignment {
  id: string
  status: string
}

const MEDICAL = "Medical history"
const SUBSTANCE = "Substance use"
const CONDITIONS = "Do you have any ongoing health conditions?"
const MEDICINES = "Which medicines do you take?"
const DRINK = "Do you drink alcohol?"

/** Publish a form of two sections, three questions between them. */
async function publishSectionedForm(api: ApiClient): Promise<string> {
  const template = await api.post<IntakeTemplate>("/api/intake/templates", {
    name: `Health and habits ${Date.now().toString(36)}`,
  })
  const draftId = template.versions[0].id

  await api.put(`/api/intake/templates/${template.id}/versions/${draftId}/items`, {
    items: [
      { key: "medical_part", item_type: "section", label: null, config: { title: MEDICAL } },
      { key: "conditions", item_type: "free_text", label: CONDITIONS, config: {} },
      { key: "medicines", item_type: "free_text", label: MEDICINES, config: {} },
      { key: "substance_part", item_type: "section", label: null, config: { title: SUBSTANCE } },
      { key: "drink", item_type: "yes_no", label: DRINK, config: {} },
    ],
  })

  const published = await api.post<IntakeVersion>(
    `/api/intake/templates/${template.id}/versions/${draftId}/publish`,
  )
  expect(published.published_at).not.toBeNull()
  return published.id
}

test("a form's sections are walked as named parts", async ({ api, page }) => {
  const stamp = Date.now().toString(36)
  const email = `parts-${stamp}@example.com`
  const phone = `+1555${`${Date.now()}`.slice(-7)}`

  const patient = await givePatient(api, { email, phone, date_of_birth: "1987-02-14" })
  const assignment = await api.post<Assignment>(`/api/patients/${patient.id}/intake-assignments`, {
    version_id: await publishSectionedForm(api),
  })

  await signInToPortal(page, await givePortalInvitation(api, patient.id, email, phone))
  await openPortalSection(page, "forms")
  await page
    .getByTestId(`forms-list-row-${assignment.id}`)
    .getByTestId("forms-list-open")
    .click()

  await test.step("the first part opens on its first question, not on its heading", async () => {
    await expect(page.getByRole("heading", { name: CONDITIONS })).toBeVisible()
    await expect(page.getByTestId("forms-item-section")).toHaveCount(0)
    await expect(page.getByTestId("forms-part-count")).toHaveText("Part 1 of 2")
    await expect(page.getByTestId("forms-part-title")).toHaveText(MEDICAL)
    await expect(page.getByTestId("forms-progress")).toHaveText("1 of 2")

    await page.getByTestId("forms-free-text").fill("Asthma.")
    await page.getByTestId("forms-continue").click()

    await expect(page.getByRole("heading", { name: MEDICINES })).toBeVisible()
    await expect(page.getByTestId("forms-progress")).toHaveText("2 of 2")
    await page.getByTestId("forms-free-text").fill("An inhaler when I need it.")
    await page.getByTestId("forms-continue").click()
  })

  await test.step("the next section is the next part, with its own count", async () => {
    await expect(page.getByRole("heading", { name: DRINK })).toBeVisible()
    await expect(page.getByTestId("forms-item-section")).toHaveCount(0)
    await expect(page.getByTestId("forms-part-count")).toHaveText("Part 2 of 2")
    await expect(page.getByTestId("forms-part-title")).toHaveText(SUBSTANCE)
    await expect(page.getByTestId("forms-progress")).toHaveText("1 of 1")

    await page.getByTestId("forms-yes-no").getByText("No", { exact: true }).click()
    await page.getByTestId("forms-continue").click()
  })

  await test.step("the review screen groups answers by part, and the form goes in", async () => {
    const review = page.getByTestId("forms-review")
    await expect(review).toBeVisible()
    await expect(review.getByTestId("forms-review-part-title")).toHaveText([MEDICAL, SUBSTANCE])

    const parts = review.getByTestId("forms-review-part")
    await expect(parts.nth(0)).toContainText(CONDITIONS)
    await expect(parts.nth(0)).toContainText(MEDICINES)
    await expect(parts.nth(1)).toContainText(DRINK)
    await expect(parts.nth(1)).not.toContainText(CONDITIONS)

    await page.getByTestId("forms-submit").click()
    await expect(page.getByTestId("forms-receipt-code")).toHaveText(/^[2-9A-HJ-NP-TV-Z]{8}$/)
  })
})
