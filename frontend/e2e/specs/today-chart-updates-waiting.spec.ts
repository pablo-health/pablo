// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Today says when a scheduled client's chart has updates waiting.
 *
 * An archive import proposes chart updates from the client's notes, and they
 * wait on the chart until the clinician decides them. Before this, nothing
 * said they were there until the chart was opened. The spec imports the
 * captured export with one more progress note the stand-in model reads as a
 * chart statement (as the import chart spec does), books the client for
 * today, and follows the count on Today's row to the chart's waiting strip.
 * Accepting the update takes the count off the row.
 *
 * The stack reads imports through its stand-in (scripts/fake_llm.py): the
 * proposal call answers a numbered "Client: Update on <key>: <text>"
 * paragraph with a proposal for that field citing it.
 */

import { randomBytes } from "node:crypto"
import type { Page } from "@playwright/test"

import type { ApiClient } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"
import {
  MAIN_EXPORT,
  deletePatient,
  exportZip,
  patientsNamed,
  progressNotePdf,
  undoAppliedImports,
} from "../fixtures/importArchive"
import { givePatient } from "../fixtures/scenarios"

const IMPORT_URL = "/dashboard/settings/import"

type RunDetail = { report: { chart_updates?: { clients: number; checked: number } } | null }

// Early on the browser's own calendar day, so the visit is on Today whatever
// the hour the suite runs; a random minute keeps reruns from overlapping.
async function bookToday(page: Page, api: ApiClient, patientId: string, title: string) {
  const minute = Math.floor(Math.random() * 50)
  const startAt = await page.evaluate((m) => {
    const d = new Date()
    d.setHours(5, m, 0, 0)
    return d.toISOString()
  }, minute)
  const endAt = new Date(new Date(startAt).getTime() + 5 * 60 * 1000).toISOString()
  return api.post<{ id: string }>("/api/appointments", {
    patient_id: patientId,
    title,
    start_at: startAt,
    end_at: endAt,
    duration_minutes: 5,
    session_type: "individual",
  })
}

test("Today counts a client's chart updates waiting and follows them to the chart", async ({
  api,
  signedInPage: page,
}) => {
  test.setTimeout(300_000)
  await undoAppliedImports(api)
  for (const leftover of await patientsNamed(api, "Llama")) await deletePatient(api, leftover.id)
  for (const leftover of await patientsNamed(api, "Bear")) await deletePatient(api, leftover.id)
  const existing = await givePatient(api, { first_name: "Lulu", last_name: "Llama" })

  const status = await api.get<{ import_prompted_at: string | null }>("/api/users/me/status")
  if (status.import_prompted_at === null) {
    await page.goto("/onboarding/import-source")
    await page.getByRole("button", { name: "Continue" }).click()
    await page.waitForURL(`**${IMPORT_URL}`)
  } else {
    await page.goto(IMPORT_URL)
  }
  await expect(page.getByRole("heading", { name: "Import from SimplePractice" })).toBeVisible()
  const extra = progressNotePdf("Client: Update on supports: Sister and a neighbor.")
  await page.getByTestId("import-archive-input").setInputFiles(
    await exportZip(MAIN_EXPORT, {
      "Medical Records/Lulu Llama/Progress Note 2026-09-28 140000 1009999991.pdf": extra,
    }),
  )
  await expect(page.getByTestId("import-summary")).toBeVisible({ timeout: 90_000 })
  await page.getByLabel("Same person — add to the existing client").check()
  const record = page.getByTestId("same-name-record")
  await record.getByRole("button", { name: "View" }).click()
  await record.getByLabel(/Pablo A\. Bear · born 2025-01-01/).check()
  await page.getByLabel("Avery Provider is me").check()
  await page.getByRole("button", { name: "Import", exact: true }).click()
  await expect(page.getByTestId("import-receipt")).toBeVisible({ timeout: 90_000 })

  // The proposals follow the import; wait until the run says they are in.
  const { runs } = await api.get<{ runs: { id: string; state: string }[] }>("/api/migration/runs")
  const applied = runs.find((r) => r.state === "applied")
  expect(applied).toBeTruthy()
  await expect
    .poll(
      async () =>
        (await api.get<RunDetail>(`/api/migration/runs/${applied!.id}`)).report?.chart_updates
          ?.clients,
      { timeout: 120_000 },
    )
    .toBe(1)

  const title = `Chart updates visit ${randomBytes(3).toString("hex")}`
  await page.goto("/dashboard")
  const appointment = await bookToday(page, api, existing.id, title)
  try {
    await page.goto("/dashboard")
    const row = page.getByRole("listitem").filter({ hasText: title })
    const waiting = row.getByRole("link", { name: "1 chart update waiting" })
    await expect(waiting).toBeVisible()
    // The heading line counts every client with something waiting; other
    // specs share the practice, so this one is at least among them.
    await expect(page.getByTestId("today-chart-updates-total")).toContainText(
      /chart updates waiting/,
    )

    await waiting.click()
    await page.waitForURL(`**/dashboard/patients/${existing.id}`)
    const strip = page.getByRole("region", { name: "Chart updates waiting" })
    await expect(strip).toContainText("1 chart update waiting from 1 note")
    const supports = strip.getByRole("listitem", { name: "Social history and supports: Supports" })
    await supports.getByRole("button", { name: "Accept" }).click()
    await expect(strip).toBeHidden()

    // Back on Today, the row no longer says anything is waiting.
    await page.goBack()
    await page.waitForURL("**/dashboard")
    const after = page.getByRole("listitem").filter({ hasText: title })
    await expect(after).toBeVisible()
    await expect(after).not.toContainText("chart update")
  } finally {
    await api.delete(`/api/appointments/${appointment.id}`)
  }
})
