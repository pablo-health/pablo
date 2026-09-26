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
 * it. The archive case also uploads a document through the chart's own
 * Documents tab, and the copy of it in the archive is held to the bytes that
 * were uploaded.
 */

import { createHash } from "node:crypto"
import { readFile } from "node:fs/promises"
import type { Download, Page, Response } from "@playwright/test"
import Ajv2020 from "ajv/dist/2020.js"
import addFormats from "ajv-formats"
import JSZip from "jszip"
import { expect, test } from "../fixtures/auth"
import { giveTranscribedSession, givePatient, giveVisitReadyToBill } from "../fixtures/scenarios"
import { fixtureFile, sha256, toInputFile } from "../fixtures/upload"

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
  format: "Archive" | "JSON" | "PDF",
  options: { transcripts?: boolean } = {},
): Promise<SavedExport> {
  await page.getByRole("button", { name: "Export", exact: true }).click()
  const dialog = page.getByRole("dialog", { name: "Export this chart" })
  await expect(dialog).toBeVisible()

  await expect(
    dialog.getByRole("button", { name: /^Archive/ }),
    "the archive is the default choice",
  ).toHaveAttribute("aria-pressed", "true")
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

const ARCHIVE_FILES = ["README.txt", "chart.pdf", "manifest.json", "patient.json", "schema.json"]

interface ExportedDocument {
  id: string
  category: string
  filename: string
  content_type: string
  bytes: number
  sha256: string
  uploaded_by: string
  archive_path: string
}

interface ArchiveManifest {
  schema_version: string
  options: ExportedChart["options"]
  files: { path: string; bytes: number; sha256: string; kind: string }[]
}

/** Every file in the ZIP, by name. */
async function unzip(bytes: Buffer): Promise<Map<string, Buffer>> {
  const archive = await JSZip.loadAsync(bytes)
  const files = new Map<string, Buffer>()
  for (const [name, entry] of Object.entries(archive.files)) {
    if (!entry.dir) files.set(name, await entry.async("nodebuffer"))
  }
  return files
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

  test("the default export is an archive whose data validates against the schema it ships", async ({
    api,
    signedInPage: page,
  }) => {
    const marker = Date.now().toString(36)
    const planSentinel = `Walk the harbor path ${marker}`
    const transcriptSentinel = `The gulls were loud today ${marker}`

    const patient = await givePatient(api)
    const visit = await giveVisitReadyToBill(api, patient.id, VISIT, planSentinel)
    await giveTranscribedSession(
      api,
      patient.id,
      `Clinician: What stood out?\nClient: ${transcriptSentinel}.`,
    )

    await page.goto(`/dashboard/patients/${patient.id}`)
    await expect(
      page.getByRole("heading", { name: `${patient.first_name} ${patient.last_name}` }),
    ).toBeVisible()

    // A document filed on the chart the way a clinician files one.
    const records = fixtureFile("records.pdf", "application/pdf")
    await page.getByRole("tab", { name: /Documents/ }).click()
    await page.getByTestId("patient-document-file-input").setInputFiles(toInputFile(records))
    await expect(
      page.getByRole("listitem").filter({ hasText: records.name }),
      "the upload lands on the chart",
    ).toBeVisible()

    const zip = await exportFromChart(page, "Archive")
    expect(zip.response.url()).toContain("format=zip")
    expect(zip.response.headers()["content-type"]).toContain("application/zip")
    const zipName = dispositionFilename(zip.response)
    expect(zipName).toMatch(new RegExp(`^patient_${patient.id}_export_\\d{4}-\\d{2}-\\d{2}\\.zip$`))
    expect(zip.download.suggestedFilename()).toBe(zipName)

    const files = await unzip(zip.bytes)
    const read = (name: string): Buffer => {
      const data = files.get(name)
      if (!data) throw new Error(`${name} is missing from the archive`)
      return data
    }

    // patient.json against the schema.json shipped beside it.
    const schema = JSON.parse(read("schema.json").toString("utf8")) as object
    const ajv = new Ajv2020({ allErrors: true })
    addFormats(ajv)
    const validate = ajv.compile(schema)
    const text = read("patient.json").toString("utf8")
    const document = JSON.parse(text) as {
      schema_version: string
      options: ExportedChart["options"]
      patient: { identifier: string }
      sessions: {
        id: string
        transcript?: unknown
        document_reference: { finalized_at: string | null } | null
      }[]
      documents: ExportedDocument[]
    }
    expect(validate(document), JSON.stringify(validate.errors)).toBe(true)

    // The uploaded document is in the archive as the bytes that were sent.
    expect(document.documents).toHaveLength(1)
    const [uploaded] = document.documents
    expect(uploaded).toMatchObject({
      category: "chart",
      filename: records.name,
      content_type: "application/pdf",
      bytes: records.body.length,
      sha256: sha256(records.body),
      uploaded_by: "clinician",
      archive_path: `documents/${uploaded.id}__${records.name}`,
    })
    expect(sha256(read(uploaded.archive_path)), "the extracted file is the uploaded file").toBe(
      sha256(records.body),
    )
    expect([...files.keys()].sort()).toEqual([...ARCHIVE_FILES, uploaded.archive_path].sort())
    expect(document.schema_version).toBe("1.0")
    expect(document.options).toEqual({
      include_transcripts: false,
      include_psychotherapy_notes: false,
    })
    expect(document.patient.identifier).toBe(patient.id)
    const noted = document.sessions.find((session) => session.id === visit.sessionId)
    expect(noted?.document_reference?.finalized_at, "the seeded note is exported finalized").toBeTruthy()
    for (const session of document.sessions) {
      expect(session, "no transcript key by default").not.toHaveProperty("transcript")
    }
    expect(text).toContain(planSentinel)
    expect(text).not.toContain(transcriptSentinel)

    // The manifest names every other file, with its true size and checksum.
    const manifest = JSON.parse(read("manifest.json").toString("utf8")) as ArchiveManifest
    expect(manifest.schema_version).toBe("1.0")
    expect(manifest.options).toEqual(document.options)
    expect(manifest.files.map((file) => file.path).sort()).toEqual(
      [...files.keys()].filter((name) => name !== "manifest.json").sort(),
    )
    expect(manifest.files.find((file) => file.path === uploaded.archive_path)?.kind).toBe(
      "document",
    )
    for (const file of manifest.files) {
      const data = read(file.path)
      expect(file.bytes, file.path).toBe(data.length)
      expect(file.sha256, file.path).toBe(createHash("sha256").update(data).digest("hex"))
    }

    expect(read("chart.pdf").subarray(0, 4).toString("latin1")).toBe("%PDF")
    expect(read("README.txt").toString("utf8")).toContain("patient.json")
    expect(read("README.txt").toString("utf8")).toContain(
      `Schema version: ${document.schema_version} `,
    )
  })
})
