// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { test, expect } from "../fixtures/auth"
import { givePatient, giveTranscribedSession, giveVisitReadyToBill } from "../fixtures/scenarios"

test("the patient chart renders without browser errors", async ({ signedInPage: page, api }) => {
  const patient = await givePatient(api)
  const pageErrors: string[] = []
  const consoleErrors: string[] = []
  page.on("pageerror", (error) => pageErrors.push(error.message))
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource:")) {
      consoleErrors.push(message.text())
    }
  })

  await page.goto(`/dashboard/patients/${patient.id}`)
  await expect(
    page.getByRole("heading", { name: `${patient.first_name} ${patient.last_name}` }),
  ).toBeVisible()
  await expect(page.getByRole("heading", { name: "Chart", exact: true })).toBeVisible()
  await expect(page.getByRole("tab", { name: /Notes/ })).toBeVisible()
  await expect(page.getByRole("tab", { name: /Documents/ })).toBeVisible()
  expect(pageErrors).toEqual([])
  expect(consoleErrors).toEqual([])
})

/**
 * What a client's chart shows, by the labels on the screen: the header facts,
 * the Chart card's tabs, and the Notes tab both empty and with notes. Each
 * assertion names the label a clinician reads, so a renamed label fails here.
 */
test.describe("What is in a client's chart", () => {
  test("a new chart's header and its tabs", async ({ signedInPage: page, api }) => {
    const patient = await givePatient(api, {
      email: `chart-${Date.now()}@example.com`,
      phone: "(555) 010-0199",
      date_of_birth: "1990-04-12",
    })
    await page.goto(`/dashboard/patients/${patient.id}`)
    await expect(
      page.getByRole("heading", { name: `${patient.first_name} ${patient.last_name}` }),
    ).toBeVisible()
    await expect(page.getByRole("link", { name: /back to clients/i })).toBeVisible()

    // The header: contact details, date of birth and the session count.
    await expect(page.getByText(patient.email ?? "")).toBeVisible()
    // The number is kept in +1 form, and the header shows it that way.
    await expect(page.getByText("+15550100199")).toBeVisible()
    await expect(page.getByText(/^DOB: \S+/)).toBeVisible()
    await expect(page.getByText("Total Sessions: 0")).toBeVisible()
    // Last and next session appear only once there is one.
    await expect(page.getByText(/^Last Session:/)).toHaveCount(0)
    await expect(page.getByText(/^Next Session:/)).toHaveCount(0)

    // Diagnoses and allergies say so when nothing is recorded yet.
    await expect(page.getByTestId("chart-diagnoses")).toHaveText("Diagnoses:None recorded")
    await expect(page.getByTestId("chart-allergies")).toContainText("Allergies:Not recorded")

    // The Chart card's tabs, in order, opening on Notes.
    const tabs = page.getByRole("tab")
    await expect(tabs).toHaveText([
      "Notes",
      "Intake",
      "Documents",
      "Measures",
      "Homework",
      "Problems",
      "History",
      "Diagnoses",
      "Medications",
      "Billing",
    ])
    await expect(page.getByRole("tab", { name: "Notes" })).toHaveAttribute("data-state", "active")
    const notes = page.getByRole("tabpanel")
    await expect(notes.getByText("No notes yet for this client.")).toBeVisible()
    await expect(notes.getByRole("button", { name: /new note/i })).toBeVisible()
  })

  test("Edit allergies changes what the header says", async ({ signedInPage: page, api }) => {
    const patient = await givePatient(api)
    await page.goto(`/dashboard/patients/${patient.id}`)
    await expect(page.getByTestId("chart-allergies")).toContainText("Allergies:Not recorded")

    await page.getByRole("button", { name: "Edit allergies" }).click()
    await page.getByRole("radio", { name: "No known drug allergies" }).check()
    await page.getByRole("button", { name: "Save" }).click()
    await expect(page.getByTestId("chart-allergies")).toContainText(
      "Allergies:No known drug allergies",
    )

    await page.getByRole("button", { name: "Edit allergies" }).click()
    await page.getByRole("radio", { name: "Allergies", exact: true }).check()
    await page.getByLabel("Allergy 1").fill("Penicillin")
    await page.getByLabel("Reaction 1").fill("hives")
    await page.getByRole("button", { name: "Save" }).click()
    await expect(page.getByTestId("chart-allergies")).toContainText(
      "Allergies:Penicillin (hives)",
    )

    await page.reload()
    await expect(page.getByTestId("chart-allergies")).toContainText(
      "Allergies:Penicillin (hives)",
    )
  })

  test("a diagnosis added on the Problems tab shows in the header", async ({
    signedInPage: page,
    api,
  }) => {
    const patient = await givePatient(api)
    await page.goto(`/dashboard/patients/${patient.id}`)
    await page.getByRole("tab", { name: /Problems/ }).click()
    await expect(page.getByText("No diagnoses recorded.")).toBeVisible()

    const form = page.getByRole("form", { name: "Add a diagnosis" })
    await form.getByLabel("Diagnosis").fill("Generalized anxiety disorder")
    await form.getByLabel("ICD-10 code").fill("F41.1")
    await form.getByRole("button", { name: "Add" }).click()

    await expect(
      page.getByTestId("problem-row").filter({ hasText: "Generalized anxiety disorder" }),
    ).toBeVisible()
    await expect(page.getByTestId("chart-diagnoses")).toHaveText(
      "Diagnoses:Generalized anxiety disorder (F41.1)",
    )
  })

  test("the Notes tab shows the three most recent notes and links to the rest", async ({
    signedInPage: page,
    api,
  }) => {
    const patient = await givePatient(api)
    for (let i = 0; i < 4; i++) {
      await giveVisitReadyToBill(api, patient.id, {
        service_code: "90834",
        diagnosis_codes: ["F41.1"],
        place_of_service: "11",
        unit_count: 1,
      })
    }
    await page.goto(`/dashboard/patients/${patient.id}`)

    await expect(page.getByRole("tab", { name: "Notes 4" })).toBeVisible()
    const notes = page.getByRole("tabpanel")
    const rows = notes.getByRole("listitem")
    await expect(rows).toHaveCount(3)
    for (const row of await rows.all()) {
      await expect(row).toContainText("Finalized")
    }
    await expect(notes.getByRole("link", { name: "View all notes" })).toBeVisible()
    await expect(notes.getByRole("button", { name: /new note/i })).toBeVisible()
  })

  test("Last Session appears once a session has been added to the chart", async ({
    signedInPage: page,
    api,
  }) => {
    const patient = await givePatient(api)
    await page.goto(`/dashboard/patients/${patient.id}`)
    await expect(page.getByText(/^Last Session:/)).toHaveCount(0)

    await giveTranscribedSession(api, patient.id, "Client reports a steady week.")
    await page.reload()
    await expect(page.getByText(/^Last Session: \S+/)).toBeVisible()
    await expect(page.getByText("Total Sessions: 1")).toBeVisible()
  })

  test("Edit on the chart opens Edit Client, which has no diagnosis field, and Update Client saves", async ({
    signedInPage: page,
    api,
  }) => {
    const patient = await givePatient(api)
    await page.goto(`/dashboard/patients/${patient.id}`)

    await page.getByRole("button", { name: "Edit", exact: true }).click()
    const dialog = page.getByRole("dialog")
    await expect(dialog.getByRole("heading", { name: "Edit Client" })).toBeVisible()
    await expect(dialog.getByLabel(/diagnosis/i)).toHaveCount(0)

    await dialog.getByLabel(/phone/i).fill("(555) 010-0123")
    await dialog.getByRole("button", { name: "Update Client" }).click()
    await expect(dialog).not.toBeVisible({ timeout: 5000 })
    await expect(page.getByText("+15550100123")).toBeVisible()
  })
})
