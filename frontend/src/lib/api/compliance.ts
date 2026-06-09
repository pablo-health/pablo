// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Compliance API
 *
 * Type-safe wrappers for /api/compliance — the per-therapist reminder
 * surface (license, malpractice, CAQH, HIPAA training, NPI, ...).
 */

import type {
  ComplianceDocument,
  ComplianceItem,
  ComplianceItemPayload,
  ComplianceTemplate,
} from "@/types/compliance"
import { del, get, getBlob, post, postForm, put } from "./client"

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

// --- Evidence documents (the credential vault) ---------------------------

export async function listComplianceDocuments(
  itemId: string,
  token?: string,
): Promise<ComplianceDocument[]> {
  return get<ComplianceDocument[]>(`/api/compliance/${itemId}/documents`, token)
}

export async function uploadComplianceDocument(
  itemId: string,
  file: File,
  documentType: string,
  description?: string,
  token?: string,
): Promise<ComplianceDocument> {
  const formData = new FormData()
  formData.append("file", file)
  formData.append("document_type", documentType)
  if (description) formData.append("description", description)
  return postForm<ComplianceDocument>(
    `/api/compliance/${itemId}/documents`,
    formData,
    token,
  )
}

export async function downloadComplianceDocument(
  documentId: string,
  token?: string,
): Promise<Blob> {
  return getBlob(`/api/compliance/documents/${documentId}/file`, token)
}

export async function deleteComplianceDocument(
  documentId: string,
  token?: string,
): Promise<void> {
  return del<void>(`/api/compliance/documents/${documentId}`, token)
}
