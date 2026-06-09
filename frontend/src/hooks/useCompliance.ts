// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  completeComplianceItem,
  createComplianceItem,
  deleteComplianceDocument,
  deleteComplianceItem,
  listComplianceDocuments,
  listComplianceItems,
  listComplianceTemplates,
  updateComplianceItem,
  uploadComplianceDocument,
} from "@/lib/api/compliance"
import { queryKeys } from "@/lib/api/queryKeys"
import type { ComplianceItemPayload } from "@/types/compliance"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export function useComplianceTemplates(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.compliance.templates(),
    queryFn: () => listComplianceTemplates(token),
    // Catalog is registry-driven and rarely changes — cache for 10 min.
    staleTime: 10 * 60 * 1000,
  })
}

export function useComplianceItems(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.compliance.items(),
    queryFn: () => listComplianceItems(token),
  })
}

export function useCreateComplianceItem(token?: string) {
  return useAuthMutation({
    mutationFn: (payload: ComplianceItemPayload) =>
      createComplianceItem(payload, token),
    invalidateKeys: [queryKeys.compliance.items()],
  })
}

export function useUpdateComplianceItem(token?: string) {
  return useAuthMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: string
      payload: ComplianceItemPayload
    }) => updateComplianceItem(id, payload, token),
    invalidateKeys: [queryKeys.compliance.items()],
  })
}

export function useCompleteComplianceItem(token?: string) {
  return useAuthMutation({
    mutationFn: (id: string) => completeComplianceItem(id, token),
    invalidateKeys: [queryKeys.compliance.items()],
  })
}

export function useDeleteComplianceItem(token?: string) {
  return useAuthMutation({
    mutationFn: (id: string) => deleteComplianceItem(id, token),
    invalidateKeys: [queryKeys.compliance.items()],
  })
}

// --- Evidence documents (the credential vault) ---------------------------

export function useComplianceDocuments(
  itemId: string,
  enabled = true,
  token?: string,
) {
  return useAuthQuery({
    queryKey: queryKeys.compliance.documents(itemId),
    queryFn: () => listComplianceDocuments(itemId, token),
    enabled: enabled && Boolean(itemId),
  })
}

export function useUploadComplianceDocument(itemId: string, token?: string) {
  return useAuthMutation({
    mutationFn: ({
      file,
      documentType,
      description,
    }: {
      file: File
      documentType: string
      description?: string
    }) =>
      uploadComplianceDocument(itemId, file, documentType, description, token),
    invalidateKeys: [queryKeys.compliance.documents(itemId)],
  })
}

export function useDeleteComplianceDocument(itemId: string, token?: string) {
  return useAuthMutation({
    mutationFn: (documentId: string) =>
      deleteComplianceDocument(documentId, token),
    invalidateKeys: [queryKeys.compliance.documents(itemId)],
  })
}
