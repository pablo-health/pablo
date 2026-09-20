// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Intake form builder API functions.
 *
 * The clinician's side of intake — building the form, not answering it. See
 * backend/app/routes/intake_packets.py.
 */

import type {
  IntakeItemInput,
  IntakeTemplate,
  IntakeVersionDetail,
} from "@/types/intakePackets"
import { get, patch, post, put } from "./client"

const ENDPOINT = "/api/intake/templates"

export async function listIntakeTemplates(token?: string): Promise<IntakeTemplate[]> {
  return get<IntakeTemplate[]>(ENDPOINT, token)
}

export async function createIntakeTemplate(
  name: string,
  token?: string
): Promise<IntakeTemplate> {
  return post<IntakeTemplate>(ENDPOINT, { name }, token)
}

export async function updateIntakeTemplate(
  templateId: string,
  data: { name?: string; archived?: boolean },
  token?: string
): Promise<IntakeTemplate> {
  return patch<IntakeTemplate>(`${ENDPOINT}/${templateId}`, data, token)
}

export async function getIntakeVersion(
  templateId: string,
  versionId: string,
  token?: string
): Promise<IntakeVersionDetail> {
  return get<IntakeVersionDetail>(`${ENDPOINT}/${templateId}/versions/${versionId}`, token)
}

/**
 * Start a draft from the current version's questions. A form that already has
 * an unpublished draft hands that one back rather than stacking a second.
 */
export async function createIntakeVersion(
  templateId: string,
  token?: string
): Promise<IntakeVersionDetail> {
  return post<IntakeVersionDetail>(`${ENDPOINT}/${templateId}/versions`, {}, token)
}

export async function replaceIntakeItems(
  templateId: string,
  versionId: string,
  items: IntakeItemInput[],
  token?: string
): Promise<IntakeVersionDetail> {
  return put<IntakeVersionDetail>(
    `${ENDPOINT}/${templateId}/versions/${versionId}/items`,
    { items },
    token
  )
}

export async function publishIntakeVersion(
  templateId: string,
  versionId: string,
  token?: string
): Promise<IntakeVersionDetail> {
  return post<IntakeVersionDetail>(
    `${ENDPOINT}/${templateId}/versions/${versionId}/publish`,
    {},
    token
  )
}
