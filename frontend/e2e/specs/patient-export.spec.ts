// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A clinician exports a chart from the chart, and the file is the chart.
 *
 * The setup goes through the API: a patient, one visit whose note is
 * finalized with a phrase nobody else would write in its plan, and one
 * session whose transcript carries a second phrase. The export itself is
 * driven the way a clinician does it — the Export action on the chart, the
 * format, the options, the download — and every claim is checked against the
 * bytes the browser saved and the response they came in on, so what is
 * proven is the file a practice would hand over, not the dialog's opinion of
 * it.
 */

import { readFile } from "node:fs/promises"
import type { Download, Page, Response } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import { giveTranscribedSession, givePatient, giveVisitReadyToBill } from "../fixtures/scenarios"

interface ExportedSession {
  id: string
  transcript?: { format: string; content: string }
  finalized_at: string | null
}

interface ExportedChart {
  patient: { id: string }
  sessions: ExportedSession[]
  options: { include_transcripts: boolean; include_psychotherapy_notes: boolean }
}

interface SavedExport {
  response: Response
  download: Download
  bytes: Buffer
}

const VISIT = {
  service_code: "90834",
  place_of_service: "11" as const,
  diagnosis_codes: ["F41.1"],
}

/**
 * Open the dialog from the chart, make the choices, and keep what the browser
 * saved together with the response it came from.
 */
async function exportFromChart(
  page: Page,
  format: "JSON" | "PDF",
  options: { transcripts?: boolean } = {},
): Promise<SavedExport> {
  await page.getByRole("button", { name: "Export", exact: true }).click()
  const dialog = page.getByRole("dialog", { name: "Export this chart" })
  await expect(dialog).toBeVisible()

  await dialog.getByRole("button", { name: new RegExp(`^${format}`) }).click()
  const transcripts = dialog.getByRole("checkbox", { name: "Include session transcripts" })
  await expect(transcripts).not.toBeChecked()
  await expect(
    dialog.getByRole("checkbox", { name: "Include psychotherapy notes" }),
  ).not.toBeChecked()
  if (options.transcripts) await transcripts.check()

  await dialog.getByRole("button", { name: "Continue" }).click()
  await expect(dialog).not.toContainText(/HIPAA|CFR|164\./)

  const served = page.waitForResponse(
    (response) =>
      /\/api\/patients\/[^/]+\/export\?/.test(response.url()) &&
      response.request().method() === "GET",
  )
  const saved = page.waitForEvent("download")
  await dialog.getByRole("button", { name: "Download" }).click()
  const [response, download] = await Promise.all([served, saved])
  expect(response.status(), `the export is served (${response.status()})`).toBe(200)

  const path = await download.path()
  const bytes = await readFile(path)
  await expect(dialog.getByText("Your download has started.")).toBeVisible()
  await dialog.getByRole("button", { name: "Close", exact: true }).click()
  await expect(dialog).toBeHidden()
  return { response, download, bytes }
}

/** The name in a Content-Disposition header, if the response sent one. */
function dispositionFilename(response: Response): string | null {
  const header = response.headers()["content-disposition"]
  return header?.match(/filename="([^"]+)"/)?.[1] ?? null
}

test.describe("patient export", () => {
  test("the chart's Export action downloads the chart as JSON and as PDF", async ({
    api,
    signedInPage: page,
  }) => {
    const marker = Date.now().toString(36)
    const planSentinel = `Revisit the lighthouse plan ${marker}`
    const transcriptSentinel = `The ferry was late again ${marker}`

    const patient = await givePatient(api)
    const visit = await giveVisitReadyToBill(api, patient.id, VISIT, planSentinel)
    const transcribed = await giveTranscribedSession(
      api,
      patient.id,
      `Clinician: How was the week?\nClient: ${transcriptSentinel}.`,
    )

    await page.goto(`/dashboard/patients/${patient.id}`)
    await expect(
      page.getByRole("heading", { name: `${patient.first_name} ${patient.last_name}` }),
    ).toBeVisible()

    // --- JSON, as the defaults leave it ------------------------------------
    const json = await exportFromChart(page, "JSON")
    expect(json.response.url()).toContain("format=json")
    expect(json.response.url()).toContain("include_transcripts=false")
    expect(json.response.url()).toContain("include_psychotherapy_notes=false")
    expect(json.response.headers()["content-type"]).toContain("application/json")

    const served = dispositionFilename(json.response)
    if (served) {
      expect(json.download.suggestedFilename()).toBe(served)
    } else {
      expect(json.download.suggestedFilename()).toMatch(
        new RegExp(`^patient_${patient.id}_export_\\d{4}-\\d{2}-\\d{2}\\.json$`),
      )
    }

    const text = json.bytes.toString("utf8")
    const chart = JSON.parse(text) as ExportedChart
    expect(chart.patient.id).toBe(patient.id)
    expect(chart.options).toEqual({
      include_transcripts: false,
      include_psychotherapy_notes: false,
    })
    expect(text).toContain(planSentinel)
    const noted = chart.sessions.find((session) => session.id === visit.sessionId)
    expect(noted?.finalized_at, "the seeded note is exported finalized").toBeTruthy()
    expect(chart.sessions.map((session) => session.id)).toContain(transcribed.id)
    for (const session of chart.sessions) {
      expect(session, "no transcript key by default").not.toHaveProperty("transcript")
    }
    expect(text).not.toContain(transcriptSentinel)

    // --- PDF ----------------------------------------------------------------
    const pdf = await exportFromChart(page, "PDF")
    expect(pdf.response.url()).toContain("format=pdf")
    expect(pdf.response.headers()["content-type"]).toContain("application/pdf")
    const pdfName = dispositionFilename(pdf.response)
    expect(pdfName, "the PDF names itself").toBeTruthy()
    expect(pdf.download.suggestedFilename()).toBe(pdfName)
    expect(pdf.bytes.subarray(0, 4).toString("latin1")).toBe("%PDF")
    expect(pdf.bytes.length, "a PDF with a chart in it, not an empty shell").toBeGreaterThan(1500)

    // --- JSON with transcripts ---------------------------------------------
    const withTranscripts = await exportFromChart(page, "JSON", { transcripts: true })
    expect(withTranscripts.response.url()).toContain("include_transcripts=true")
    const fuller = JSON.parse(withTranscripts.bytes.toString("utf8")) as ExportedChart
    expect(fuller.options.include_transcripts).toBe(true)
    const heard = fuller.sessions.find((session) => session.id === transcribed.id)
    expect(heard?.transcript?.content).toContain(transcriptSentinel)
    expect(withTranscripts.bytes.toString("utf8")).toContain(planSentinel)
  })
})
