// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A blank psychiatric follow-up started from "New note": the dialog asks for
 * the place of service the type requires, flags it when Create is pressed
 * without one, and creates the note with the value chosen.
 *
 * Catches the blank-note path posting only the note type, which the server
 * refuses for any type with a required input — every attempt failed and no
 * note was written.
 */

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

type Note = { id: string; note_type: string; note_inputs: Record<string, string> | null }

const FOLLOW_UP_LABEL = "Psychiatric follow-up (E/M + psychotherapy)"

test("a blank psychiatric follow-up asks for its place of service, then is created with it", async ({
  api,
  signedInPage: page,
}) => {
  const patient = await givePatient(api)

  await page.goto(`/dashboard/patients/${patient.id}`)
  await page.getByLabel("Notes").getByRole("button", { name: "New note" }).click()
  const dialog = page.getByRole("dialog")
  await dialog.getByRole("button", { name: new RegExp(`^${escapeRegExp(FOLLOW_UP_LABEL)}`) }).click()

  await expect(dialog.getByRole("heading", { name: FOLLOW_UP_LABEL })).toBeVisible()
  await dialog.getByRole("button", { name: "Create" }).click()
  await expect(dialog.getByText("Place of service is required")).toBeVisible()
  await expect(page).toHaveURL(new RegExp(`/dashboard/patients/${patient.id}$`))

  await dialog.getByRole("combobox", { name: "Place of service" }).click()
  await page.getByRole("option", { name: "In office" }).click()

  const created = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/patients/${patient.id}/notes`) &&
      response.request().method() === "POST",
  )
  await dialog.getByRole("button", { name: "Create" }).click()
  const response = await created
  expect(response.status(), await response.text()).toBe(201)
  const note = (await response.json()) as Note
  expect(note.note_type).toBe("psychiatric_follow_up")
  expect(note.note_inputs).toMatchObject({ place_of_service: "In office" })

  await page.waitForURL(`**/dashboard/patients/${patient.id}/notes/${note.id}`)
})

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}
