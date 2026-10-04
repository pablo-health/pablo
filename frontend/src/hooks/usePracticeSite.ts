// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { QueryClient } from "@tanstack/react-query"
import {
  discardPracticeSiteDraft,
  getPracticeSite,
  previewPracticeSiteDraft,
  publishPracticeSite,
  rollBackPracticeSite,
  setPracticeSiteDraftHeader,
  uploadPracticeSiteDraft,
  type PracticeSite,
  type SiteHeader,
  type SitePreview,
} from "@/lib/api/practiceSite"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export const practiceSiteKeys = {
  all: ["practiceSite"] as const,
}

export function usePracticeSite() {
  return useAuthQuery<PracticeSite>({
    queryKey: practiceSiteKeys.all,
    queryFn: () => getPracticeSite(),
  })
}

/** Every change answers with the whole status, which replaces the cached one. */
function keep(data: PracticeSite, _variables: unknown, queryClient: QueryClient) {
  queryClient.setQueryData(practiceSiteKeys.all, data)
}

export function useUploadPracticeSiteDraft() {
  return useAuthMutation<PracticeSite, File>({
    mutationFn: (file) => uploadPracticeSiteDraft(file),
    onSuccess: keep,
  })
}

export function useSetPracticeSiteDraftHeader() {
  return useAuthMutation<PracticeSite, SiteHeader>({
    mutationFn: (header) => setPracticeSiteDraftHeader(header),
    onSuccess: keep,
  })
}

export function useDiscardPracticeSiteDraft() {
  return useAuthMutation<PracticeSite, void>({
    mutationFn: () => discardPracticeSiteDraft(),
    onSuccess: keep,
  })
}

export function usePublishPracticeSite() {
  return useAuthMutation<PracticeSite, void>({
    mutationFn: () => publishPracticeSite(),
    onSuccess: keep,
  })
}

export function useRollBackPracticeSite() {
  return useAuthMutation<PracticeSite, number>({
    mutationFn: (version) => rollBackPracticeSite(version),
    onSuccess: keep,
  })
}

export function usePreviewPracticeSiteDraft() {
  return useAuthMutation<SitePreview, void>({
    mutationFn: () => previewPracticeSiteDraft(),
  })
}
