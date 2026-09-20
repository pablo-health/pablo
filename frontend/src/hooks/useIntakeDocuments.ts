// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  createIntakeDocument,
  createIntakeDocumentVersion,
  listIntakeDocuments,
  publishIntakeDocument,
  updateIntakeDocument,
} from "@/lib/api/intakeDocuments"
import { queryKeys } from "@/lib/api/queryKeys"
import type {
  CreateDocumentInput,
  IntakeDocument,
  UpdateDocumentInput,
} from "@/types/intakeDocuments"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export function useIntakeDocuments(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.intakeDocuments.list(),
    queryFn: (): Promise<IntakeDocument[]> => listIntakeDocuments({}, token),
    staleTime: 60 * 1000,
  })
}

/**
 * The documents a form may ask somebody to sign.
 *
 * A separate query rather than a filter over the list above: a document
 * whose newest version is a draft is still perfectly askable, and the
 * server is what knows which published version that is.
 */
export function usePublishedIntakeDocuments(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.intakeDocuments.published(),
    queryFn: (): Promise<IntakeDocument[]> =>
      listIntakeDocuments({ publishedOnly: true }, token),
    staleTime: 60 * 1000,
  })
}

export function useCreateIntakeDocument(token?: string) {
  return useAuthMutation({
    mutationFn: (input: CreateDocumentInput) => createIntakeDocument(input, token),
    invalidateKeys: [queryKeys.intakeDocuments.all],
  })
}

export function useSaveIntakeDocument(token?: string) {
  return useAuthMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateDocumentInput }) =>
      updateIntakeDocument(id, input, token),
    invalidateKeys: [queryKeys.intakeDocuments.all],
  })
}

export function usePublishIntakeDocument(token?: string) {
  return useAuthMutation({
    mutationFn: (id: string) => publishIntakeDocument(id, token),
    invalidateKeys: [queryKeys.intakeDocuments.all],
  })
}

export function useNewIntakeDocumentVersion(token?: string) {
  return useAuthMutation({
    mutationFn: (id: string) => createIntakeDocumentVersion(id, token),
    invalidateKeys: [queryKeys.intakeDocuments.all],
  })
}
