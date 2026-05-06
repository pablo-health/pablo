// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Compliance API
 *
 * Type-safe wrappers for /api/compliance — the per-therapist reminder
 * surface (license, malpractice, CAQH, HIPAA training, NPI, ...).
 */

import type {
  ComplianceItem,
  ComplianceItemPayload,
  ComplianceTemplate,
} from "@/types/compliance"
import { del, get, post, put } from "./client"

export async function listComplianceTemplates(
  token?: string,
): Promise<ComplianceTemplate[]> {
  return get<ComplianceTemplate[]>("/api/compliance/templates", token)
}

export async function listComplianceItems(
  token?: string,
): Promise<ComplianceItem[]> {
  return get<ComplianceItem[]>("/api/compliance", token)
}

export async function createComplianceItem(
  payload: ComplianceItemPayload,
  token?: string,
): Promise<ComplianceItem> {
  return post<ComplianceItem>("/api/compliance", payload, token)
}

export async function updateComplianceItem(
  id: string,
  payload: ComplianceItemPayload,
  token?: string,
): Promise<ComplianceItem> {
  return put<ComplianceItem>(`/api/compliance/${id}`, payload, token)
}

export async function completeComplianceItem(
  id: string,
  token?: string,
): Promise<ComplianceItem> {
  return post<ComplianceItem>(`/api/compliance/${id}/complete`, {}, token)
}

export async function deleteComplianceItem(
  id: string,
  token?: string,
): Promise<void> {
  return del<void>(`/api/compliance/${id}`, token)
}
