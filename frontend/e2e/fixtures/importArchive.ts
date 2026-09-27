// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Helpers for the import specs.
 *
 * The archive a spec uploads is the committed, captured fixture under
 * `backend/tests/fixtures/`, zipped at run time exactly the way a practice
 * zips the folder its old system downloaded. Nothing here builds records by
 * hand; if the fixture changes, the specs see the change.
 *
 * Specs share one practice with every other spec, and a local stack is
 * reused between runs, so each import spec starts by undoing whatever an
 * earlier run of it left applied.
 */

import { readdir, readFile, stat } from "node:fs/promises"
import path from "node:path"
import { fileURLToPath } from "node:url"
import JSZip from "jszip"
import type { ApiClient } from "./api"

const FIXTURES = fileURLToPath(new URL("../../../backend/tests/fixtures/", import.meta.url))

export const MAIN_EXPORT = path.join(FIXTURES, "simplepractice_export")
export const SAME_NAME_NO_DOB_EXPORT = path.join(FIXTURES, "simplepractice_export_same_name_no_dob")

async function walk(dir: string): Promise<string[]> {
  const out: string[] = []
  for (const name of await readdir(dir)) {
    const full = path.join(dir, name)
    if ((await stat(full)).isDirectory()) out.push(...(await walk(full)))
    else out.push(full)
  }
  return out
}

/** The fixture folder as the zip a practice would upload. */
export async function exportZip(folder: string): Promise<{ name: string; mimeType: string; buffer: Buffer }> {
  const zip = new JSZip()
  const top = "Export - Complete - e2e"
  for (const file of await walk(folder)) {
    const rel = path.relative(folder, file)
    if (rel === "README.md" || rel.endsWith(".py")) continue // fixture housekeeping, not export
    zip.file(`${top}/${rel.split(path.sep).join("/")}`, await readFile(file))
  }
  const buffer = await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" })
  return { name: "export.zip", mimeType: "application/zip", buffer }
}

interface RunSummary {
  id: string
  state: string
}

interface PatientRow {
  id: string
  first_name: string
  last_name: string
  email: string | null
  date_of_birth: string | null
}

/** Undo every applied import, so a spec starts from the practice it expects. */
export async function undoAppliedImports(api: ApiClient): Promise<void> {
  const { runs } = await api.get<{ runs: RunSummary[] }>("/api/migration/runs")
  for (const run of runs.filter((r) => r.state === "applied")) {
    await api.post(`/api/migration/runs/${run.id}/undo`, { include_edited: true })
  }
}

/** Live patients whose last name matches, as the API lists them. */
export async function patientsNamed(api: ApiClient, lastName: string): Promise<PatientRow[]> {
  const page = await api.get<{ data: PatientRow[] }>(
    `/api/patients?search=${encodeURIComponent(lastName)}&page_size=100`,
  )
  return page.data.filter((p) => p.last_name === lastName)
}

export async function deletePatient(api: ApiClient, patientId: string): Promise<void> {
  await api.request("DELETE", `/api/patients/${patientId}`, { acknowledged_retention_obligation: true })
}

export async function noteCount(api: ApiClient, patientId: string): Promise<number> {
  const notes = await api.get<{ total: number }>(`/api/patients/${patientId}/notes`)
  return notes.total
}
