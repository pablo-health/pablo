// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice exports itself from Settings, and the file is the practice.
 *
 * Two clients are created through the API. The export is driven the way an
 * administrator does it: the Export page under Practice, the two choices left
 * off, one button. Every claim is checked against the bytes the browser saved:
 * a folder per client holding the archive the chart's own export builds,
 * both clients in clients.csv, the audit log, and a manifest whose checksums
 * hold. Other specs share this practice, so the file may hold more clients
 * than these two; what is asserted is that these two are in it, exactly once,
 * with the right files.
 *
 * The stack runs in development mode, where the admin and hardware-key gates
 * stand down, so the pinned user reaches the route as an administrator would.
 */

import { createHash } from "node:crypto"
import { readFile } from "node:fs/promises"
import JSZip from "jszip"
import { expect, test } from "../fixtures/auth"
import { parseCsv } from "../fixtures/csv"
import { givePatient } from "../fixtures/scenarios"

const EXPORT_URL = "/dashboard/settings/export"

interface Manifest {
  schema_version: string
  options: { include_transcripts: boolean; include_psychotherapy_notes: boolean }
  files: { path: string; bytes: number; sha256: string; kind: string }[]
}

async function unzip(bytes: Buffer): Promise<Map<string, Buffer>> {
  const archive = await JSZip.loadAsync(bytes)
  const files = new Map<string, Buffer>()
  for (const [name, entry] of Object.entries(archive.files)) {
    if (!entry.dir) files.set(name, await entry.async("nodebuffer"))
  }
  return files
}

test("the practice export holds each client's archive and the practice-wide files", async ({
  api,
  signedInPage: page,
}) => {
  const marker = Date.now().toString(36)
  const first = await givePatient(api, { last_name: `Practice-${marker}` })
  const second = await givePatient(api, { last_name: `Practice-${marker}` })

  await page.goto(EXPORT_URL)
  await expect(page.getByRole("heading", { name: "Export practice data" })).toBeVisible()
  await expect(page.getByText("Everything in this practice, as one file.")).toBeVisible()
  await expect(page.getByRole("checkbox", { name: "Include session transcripts" })).not.toBeChecked()
  await expect(
    page.getByRole("checkbox", { name: "Include psychotherapy notes" }),
  ).not.toBeChecked()

  const served = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/admin/tenant-export") && response.request().method() === "POST",
  )
  const saved = page.waitForEvent("download")
  await page.getByRole("button", { name: "Export practice data" }).click()
  const [response, download] = await Promise.all([served, saved])
  expect(response.status(), `the export is served (${response.status()})`).toBe(200)
  expect(response.headers()["content-type"]).toContain("application/zip")
  expect(download.suggestedFilename()).toBe("practice-export.zip")
  await expect(page.getByRole("status")).toHaveText("Your download has started.")

  const bytes = await readFile((await download.path()) as string)
  const files = await unzip(bytes)
  const read = (name: string): Buffer => {
    const data = files.get(name)
    if (!data) throw new Error(`${name} is missing from the practice export`)
    return data
  }

  // The two clients, once each, in clients.csv.
  const clients = parseCsv(read("clients.csv").toString("utf8"))
  for (const patient of [first, second]) {
    const rows = clients.filter((row) => row.client_id === patient.id)
    expect(rows, `${patient.id} is in clients.csv once`).toHaveLength(1)
    expect(rows[0]).toMatchObject({ first_name: patient.first_name, last_name: patient.last_name })
  }
  expect(Object.keys(clients[0])[0]).toBe("client_id")
  expect(files.has("appointments.csv")).toBe(true)
  expect(files.has("audit_log.csv")).toBe(true)

  // A folder per client holding the archive the chart's own export builds.
  for (const patient of [first, second]) {
    const folder = [...files.keys()].filter((name) => name.startsWith(`patients/${patient.id}/`))
    expect(folder, `one archive for ${patient.id}`).toHaveLength(1)
    expect(folder[0]).toMatch(new RegExp(`^patients/${patient.id}/patient_${patient.id}_export_\\d{4}-\\d{2}-\\d{2}\\.zip$`))
    const inner = await unzip(read(folder[0]))
    const document = JSON.parse(inner.get("patient.json")?.toString("utf8") ?? "{}") as {
      patient: { identifier: string }
    }
    expect(document.patient.identifier).toBe(patient.id)
    expect(inner.has("chart.pdf")).toBe(true)
  }

  // The manifest names every other file, with its true size and checksum.
  const manifest = JSON.parse(read("manifest.json").toString("utf8")) as Manifest
  expect(manifest.options).toEqual({
    include_transcripts: false,
    include_psychotherapy_notes: false,
  })
  expect(manifest.files.map((file) => file.path).sort()).toEqual(
    [...files.keys()].filter((name) => name !== "manifest.json").sort(),
  )
  for (const file of manifest.files) {
    const data = read(file.path)
    expect(file.bytes, file.path).toBe(data.length)
    expect(file.sha256, file.path).toBe(createHash("sha256").update(data).digest("hex"))
  }
  expect(manifest.files.find((file) => file.path === "clients.csv")?.kind).toBe("csv")
  expect(
    manifest.files.find((file) => file.path.startsWith(`patients/${first.id}/`))?.kind,
  ).toBe("archive")
})
