// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Credentialing checklist API
 *
 * Type-safe wrappers for /api/credentialing/checklist — the tiered question set,
 * its per-tier progress, and the Tier-0 confirmations.
 */

import type {
  Confirmation,
  ConfirmationPayload,
  ChecklistAnswers,
  ChecklistSurface,
  NppesLookup,
  NppesSearchQuery,
  NppesSearchResult,
} from "@/types/credentialing"
import { get, patch, put } from "./client"

/**
 * The question set for this clinician, with what she has already answered.
 *
 * `supervised` and `prescriber` override what the record says. The wizard
 * passes them while she is answering the fork, because the answer has to
 * narrow the same page it was given on.
 */
export async function getChecklist(
  options: { supervised?: boolean; prescriber?: boolean } = {},
  token?: string,
): Promise<ChecklistSurface> {
  const params = new URLSearchParams()
  if (options.supervised !== undefined) {
    params.set("supervised", String(options.supervised))
  }
  if (options.prescriber !== undefined) {
    params.set("prescriber", String(options.prescriber))
  }
  const query = params.toString()
  return get<ChecklistSurface>(
    `/api/credentialing/checklist${query ? `?${query}` : ""}`,
    token,
  )
}

export async function listConfirmations(
  token?: string,
): Promise<Confirmation[]> {
  return get<Confirmation[]>("/api/credentialing/checklist/confirmations", token)
}

export async function recordConfirmation(
  fieldKey: string,
  payload: ConfirmationPayload,
  token?: string,
): Promise<Confirmation> {
  return put<Confirmation>(
    `/api/credentialing/checklist/confirmations/${fieldKey}`,
    payload,
    token,
  )
}

/** Partial by design: an unmentioned answer keeps its current value. */
export async function saveChecklistAnswers(
  answers: ChecklistAnswers,
  token?: string,
): Promise<Record<string, unknown>> {
  return patch<Record<string, unknown>>(
    "/api/credentialing/checklist/answers",
    answers,
    token,
  )
}

/**
 * Look one NPI up in the public registry.
 *
 * Nothing is written by this. What comes back is presented for her to confirm,
 * and the confirmation is what promotes it onto her record.
 */
export async function lookUpNpi(npi: string, token?: string): Promise<NppesLookup> {
  return get<NppesLookup>(`/api/credentialing/nppes/${encodeURIComponent(npi)}`, token)
}

/**
 * Find a provider by name, for someone who cannot recall ten digits.
 *
 * Surname is required; state is what makes the answer usable — a common name
 * returns dozens of people nationally and one within a state.
 */
export async function searchNpi(
  query: NppesSearchQuery,
  token?: string,
): Promise<NppesSearchResult> {
  const params = new URLSearchParams({ last_name: query.last_name })
  if (query.first_name) params.set("first_name", query.first_name)
  if (query.state) params.set("state", query.state)
  return get<NppesSearchResult>(`/api/credentialing/nppes?${params.toString()}`, token)
}
