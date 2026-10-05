// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  adoptIntakeStarter,
  createIntakeDocument,
  createIntakeDocumentVersion,
  listIntakeDocuments,
  listIntakeStarters,
  publishIntakeDocument,
  updateIntakeDocument,
} from "@/lib/api/intakeDocuments"
import { queryKeys } from "@/lib/api/queryKeys"
import type {
  AdoptedStarter,
  CreateDocumentInput,
  IntakeDocument,
  IntakeStarter,
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

/** The built-in documents a practice can start from. They never change. */
export function useIntakeStarters(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.intakeDocuments.starters(),
    queryFn: (): Promise<IntakeStarter[]> => listIntakeStarters(token),
    staleTime: Infinity,
  })
}

/**
 * Adopt a starter: the practice gets a published copy of the document, so
 * the document lists refresh, and the caller gets the items to add.
 */
export function useAdoptIntakeStarter(token?: string) {
  return useAuthMutation({
    mutationFn: (key: string): Promise<AdoptedStarter> => adoptIntakeStarter(key, token),
    invalidateKeys: [queryKeys.intakeDocuments.list(), queryKeys.intakeDocuments.published()],
  })
}

export function useNewIntakeDocumentVersion(token?: string) {
  return useAuthMutation({
    mutationFn: (id: string) => createIntakeDocumentVersion(id, token),
    invalidateKeys: [queryKeys.intakeDocuments.all],
  })
}
