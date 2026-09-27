// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Importing a records-system export: upload an archive, read its preview,
 * answer its questions, apply, undo, and list what has been imported before.
 *
 * Preview and apply happen in the background on the server. Every call that
 * starts one returns the run straight away; the screen polls `getImportRun`
 * until `state` moves on.
 */

import { del, get, getBlob, post, postForm } from "./client"

export type ImportRunState =
  | "queued"
  | "previewing"
  | "previewed"
  | "applying"
  | "applied"
  | "undoing"
  | "undone"
  | "failed"

export type ImportScope = "patients" | "practice" | "both"

export type RecordState = "new" | "unchanged" | "changed" | "conflict"

export interface ImportClient {
  card_id: string
  display_name: string
  folder_name: string
  birthday: string | null
  email: string | null
  phone: string | null
  address: { street: string; city: string; state: string; postal_code: string } | null
  state: RecordState
  existing_patient_id: string | null
  match_evidence: "name_and_dob" | "email" | "ledger" | null
  possible_duplicates: string[]
}

export interface ImportRecord {
  record_type: "note" | "questionnaire" | "thread" | "upload" | "billing"
  source_id: string
  path: string
  kind: string
  label: string
  when: string | null
  card_id: string | null
  evidence: string | null
  name_disagrees: boolean
  candidates: string[]
  state: RecordState
  landable: boolean
  reason: string | null
}

export interface ImportPreview {
  source_system: string
  scope: ImportScope
  clients: ImportClient[]
  non_client_contacts: { card_id: string; display_name: string }[]
  providers: string[]
  records: ImportRecord[]
  counts: Record<string, Record<string, number>>
  cannot_land: { what: string; count: number; reason: string }[]
  questions: {
    same_name: {
      folder_name: string
      candidates: string[]
      records: { record_type: string; source_id: string }[]
    }[]
    duplicates: { card_id: string; possible_duplicates: string[] }[]
    providers: { name: string }[]
  }
  practice: {
    proposals: {
      provider_name: string | null
      visit_kind: string | null
      visit_minutes: number | null
      rate_cents: number | null
    }
    not_in_export: string[]
  }
}

export interface ImportRunSummary {
  id: string
  source_system: string
  scope: ImportScope
  state: ImportRunState
  started_at: string
  finished_at: string | null
  archive_expires_at: string | null
  has_archive: boolean
  counts: Record<string, Record<string, number>> | null
  error: string | null
}

export interface ImportRunDetail extends ImportRunSummary {
  preview: ImportPreview | null
  decisions: ImportDecisions | null
  report: {
    counts: Record<string, Record<string, number>>
    not_landed: { key: string; reason: string }[]
    patients: Record<string, string>
    undo?: { removed: Record<string, number>; kept: { key: string; reason: string }[] }
  } | null
  missing: string[]
}

export interface ImportDecisions {
  /** `"<record_type>:<source_id>"` → contact card id, or `"skip"`. */
  assignments: Record<string, string>
  /** Contact card id → `"create"` or `"merge:<patient id>"`. */
  duplicates: Record<string, string>
  /** Exported provider name → `"me"`. */
  providers: Record<string, string>
  practice: Record<string, boolean>
}

/** A run is still moving when its state is one of these; poll until it is not. */
export const IMPORT_BUSY_STATES: ReadonlySet<ImportRunState> = new Set([
  "queued",
  "previewing",
  "applying",
  "undoing",
])

export function recordKey(recordType: string, sourceId: string): string {
  return `${recordType}:${sourceId}`
}

export async function startImport(file: File, scope: ImportScope = "both"): Promise<ImportRunDetail> {
  const form = new FormData()
  form.append("file", file)
  form.append("scope", scope)
  return postForm<ImportRunDetail>("/api/migration/runs", form)
}

export async function getImportRun(runId: string): Promise<ImportRunDetail> {
  return get<ImportRunDetail>(`/api/migration/runs/${runId}`)
}

export async function listImportRuns(): Promise<{ runs: ImportRunSummary[] }> {
  return get<{ runs: ImportRunSummary[] }>("/api/migration/runs")
}

export async function applyImport(runId: string, decisions: ImportDecisions): Promise<ImportRunDetail> {
  return post<ImportRunDetail>(`/api/migration/runs/${runId}/apply`, decisions)
}

export async function undoImport(runId: string, includeEdited: boolean): Promise<ImportRunDetail> {
  return post<ImportRunDetail>(`/api/migration/runs/${runId}/undo`, { include_edited: includeEdited })
}

export async function deleteImportArchive(runId: string): Promise<ImportRunDetail> {
  return del<ImportRunDetail>(`/api/migration/runs/${runId}/archive`)
}

/** The source file behind one record, for telling same-named clients apart. */
export async function fetchImportFile(runId: string, path: string): Promise<Blob> {
  return getBlob(`/api/migration/runs/${runId}/files?path=${encodeURIComponent(path)}`)
}

