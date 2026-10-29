// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Chart history API types
 *
 * Mirrors backend `app.chart_history.schemas`. Each field holds one free-text
 * value, with the values it held before; `text` is null when nothing is
 * recorded or the value was removed.
 */

export interface HistoryRevision {
  text: string | null
  written_at: string
  written_by: string | null
  replaced_at: string
  replaced_by: string
  source_note_id: string | null
}

export interface HistoryField {
  key: string
  label: string
  text: string | null
  updated_at: string | null
  updated_by: string | null
  source_note_id: string | null
  source_note_date: string | null
  /** Most recently replaced first. */
  earlier: HistoryRevision[]
}

export interface HistoryGroup {
  key: string
  label: string
  fields: HistoryField[]
}

export interface ChartHistoryResponse {
  groups: HistoryGroup[]
}

export interface SetHistoryFieldRequest {
  text: string
  source_note_id?: string | null
}
