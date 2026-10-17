// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * An archive import proposes each client's chart from their notes.
 *
 * The captured export's notes are prose the stand-in model does not answer,
 * so the spec adds one more progress note to the archive, laid out as
 * SimplePractice prints one, whose body is a line the stand-in reads as a
 * chart statement. After the import applies, the receipt says a chart has
 * updates to review; the client's Chart card opens with the proposal, under
 * the imported note, with the export's diagnosis codes offered to the
 * problem list; accepting writes the chart.
 *
 * The stack reads imports through its stand-in (scripts/fake_llm.py): the
 * proposal call answers a numbered "Client: Update on <key>: <text>"
 * paragraph with a proposal for that field citing it.
 */

import { jsPDF } from "jspdf"

import { expect, test } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import {
  MAIN_EXPORT,
  deletePatient,
  exportZip,
  patientsNamed,
  undoAppliedImports,
} from "../fixtures/importArchive"
import { givePatient } from "../fixtures/scenarios"

const IMPORT_URL = "/dashboard/settings/import"

type HistoryField = { key: string; text: string | null }
type ChartHistory = { groups: { fields: HistoryField[] }[] }
type RunDetail = { report: { chart_updates?: { clients: number; checked: number } } | null }

async function historyText(api: ApiClient, patientId: string, key: string) {
  const chart = await api.get<ChartHistory>(`/api/patients/${patientId}/chart-history`)
  return chart.groups.flatMap((g) => g.fields).find((f) => f.key === key)?.text
}

/**
 * A progress note as SimplePractice's export prints one, for the client the
 * fixture calls Lulu L. Llama: the header the reader keys on, the title line,
 * one body paragraph, the footer. Later than every note in the capture, so it
 * is the one read for the chart.
 */
function progressNotePdf(body: string): Uint8Array {
  const doc = new jsPDF({ unit: "pt", format: "letter" })
  doc.setFont("helvetica", "normal")
  doc.setFontSize(10)
  const lines = [
    "Client:             Lulu L. Llama",
    "DOB:              03/14/1990",
    "Provider:          Avery Provider",
    "",
    "Appointment:  Individual appointment on September 28, 2026        Diagnosis:   F41.1 - Generalized anxiety disorder",
    "               2:00 pm - 3:00 pm ET, 60 min",
    "                   Billing code: 90834 - Psychotherapy, 45 min",
    "",
    "                                 Progress Note",
    "",
    body,
    "",
    "",
    "Created on Sep 28, 2026 at 3:10 pm ET. Last updated on Sep 28, 2026 at 3:10 pm ET.                                  Page 1 of 1",
  ]
  let y = 60
  for (const line of lines) {
    if (line) doc.text(line, 40, y)
    y += 14
  }
  return new Uint8Array(doc.output("arraybuffer"))
}

test("an archive import proposes the chart from each client's notes", async ({
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
  const summary = page.getByTestId("import-summary")
  await expect(summary).toBeVisible({ timeout: 90_000 })
  await expect(summary).toContainText("16 notes")
  await page.getByLabel("Same person — add to the existing client").check()
  const record = page.getByTestId("same-name-record")
  await record.getByRole("button", { name: "View" }).click()
  await record.getByLabel(/Pablo A\. Bear · born 2025-01-01/).check()
  await page.getByLabel("Avery Provider is me").check()
  await page.getByRole("button", { name: "Import", exact: true }).click()
  const receipt = page.getByTestId("import-receipt")
  await expect(receipt).toBeVisible({ timeout: 90_000 })
  await expect(receipt).toContainText("Imported 3 clients and 16 notes.")

  // The chart proposals follow the import, client by client; the receipt
  // names how many charts have something to review once they are in.
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
  // The receipt keeps reading the run until the chart check has reported.
  await expect(page.getByTestId("import-chart-updates")).toContainText(
    "Chart updates from the notes are ready to review on 1",
  )

  // Lulu's chart opens with the proposal, under the note that made it, and
  // the diagnosis the export carried offered to the problem list.
  await page.goto(`/dashboard/patients/${existing.id}`)
  const strip = page.getByRole("region", { name: "Chart updates waiting" })
  await expect(strip).toBeVisible()
  await expect(strip).toContainText("1 chart update waiting from 1 note")
  await expect(strip).toContainText("From the note of September 28, 2026, imported from SimplePractice")
  const supports = strip.getByRole("listitem", { name: "Social history and supports: Supports" })
  await expect(supports).toContainText("Sister and a neighbor.")
  await expect(supports).toContainText("¶1")
  await expect(strip.getByTestId("source-diagnosis")).toContainText("Generalized anxiety disorder")

  await supports.getByRole("button", { name: "Accept" }).click()

  await expect.poll(() => historyText(api, existing.id, "supports")).toBe("Sister and a neighbor.")
})
