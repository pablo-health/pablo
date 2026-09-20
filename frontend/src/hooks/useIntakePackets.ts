// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  createIntakeTemplate,
  createIntakeVersion,
  getIntakeVersion,
  listIntakeTemplates,
  publishIntakeVersion,
  replaceIntakeItems,
  updateIntakeTemplate,
} from "@/lib/api/intakePackets"
import { queryKeys } from "@/lib/api/queryKeys"
import type { IntakeItemInput, IntakeTemplate, IntakeVersionDetail } from "@/types/intakePackets"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export function useIntakeTemplates(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.intakeTemplates.list(),
    queryFn: (): Promise<IntakeTemplate[]> => listIntakeTemplates(token),
    staleTime: 60 * 1000,
  })
}

/**
 * One version and its questions. Disabled until a version is chosen, so the
 * caller can hold the hook unconditionally while the list is still loading.
 */
export function useIntakeVersion(templateId?: string, versionId?: string, token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.intakeTemplates.version(templateId ?? "", versionId ?? ""),
    queryFn: (): Promise<IntakeVersionDetail> =>
      getIntakeVersion(templateId as string, versionId as string, token),
    enabled: Boolean(templateId && versionId),
  })
}

export function useCreateIntakeTemplate(token?: string) {
  return useAuthMutation({
    mutationFn: (name: string) => createIntakeTemplate(name, token),
    invalidateKeys: [queryKeys.intakeTemplates.all],
  })
}

export function useUpdateIntakeTemplate(token?: string) {
  return useAuthMutation({
    mutationFn: ({ id, data }: { id: string; data: { name?: string; archived?: boolean } }) =>
      updateIntakeTemplate(id, data, token),
    invalidateKeys: [queryKeys.intakeTemplates.all],
  })
}

export function useCreateIntakeVersion(token?: string) {
  return useAuthMutation({
    mutationFn: (templateId: string) => createIntakeVersion(templateId, token),
    invalidateKeys: [queryKeys.intakeTemplates.all],
  })
}

export function useSaveIntakeItems(token?: string) {
  return useAuthMutation({
    mutationFn: ({
      templateId,
      versionId,
      items,
    }: {
      templateId: string
      versionId: string
      items: IntakeItemInput[]
    }) => replaceIntakeItems(templateId, versionId, items, token),
    invalidateKeys: [queryKeys.intakeTemplates.all],
  })
}

export function usePublishIntakeVersion(token?: string) {
  return useAuthMutation({
    mutationFn: ({ templateId, versionId }: { templateId: string; versionId: string }) =>
      publishIntakeVersion(templateId, versionId, token),
    invalidateKeys: [queryKeys.intakeTemplates.all],
  })
}
