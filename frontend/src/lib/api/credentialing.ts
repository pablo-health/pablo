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
