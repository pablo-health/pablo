// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  getInviteTemplate,
  previewClientInvite,
  previewInviteTemplate,
  resetInviteTemplate,
  saveInviteTemplate,
  type ClientInvitePreview,
  type InviteTemplate,
  type InviteTemplateDraft,
  type RenderedInvitePreview,
} from "@/lib/api/inviteTemplate"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export const inviteTemplateKeys = {
  all: ["inviteTemplate"] as const,
  preview: (draft: InviteTemplateDraft) =>
    [...inviteTemplateKeys.all, "preview", draft.subject, draft.body] as const,
  client: (patientId: string, versionIds: string[]) =>
    [...inviteTemplateKeys.all, "client", patientId, ...versionIds] as const,
}

/** The practice's invitation wording. `retry: false`: a deployment without
 * the portal answers 404, and asking again does not change that. */
export function useInviteTemplate() {
  return useAuthQuery<InviteTemplate>({
    queryKey: inviteTemplateKeys.all,
    queryFn: () => getInviteTemplate(),
    retry: false,
  })
}

export function useSaveInviteTemplate() {
  return useAuthMutation<InviteTemplate, InviteTemplateDraft>({
    mutationFn: (draft) => saveInviteTemplate(draft),
    invalidateKeys: [inviteTemplateKeys.all],
  })
}

export function useResetInviteTemplate() {
  return useAuthMutation<InviteTemplate, void>({
    mutationFn: () => resetInviteTemplate(),
    invalidateKeys: [inviteTemplateKeys.all],
  })
}

/** A draft rendered for an example client. Keyed on the draft, so the
 * preview follows the editor without a request per keystroke once the
 * caller debounces what it passes in. */
export function useInviteTemplatePreview(draft: InviteTemplateDraft | null) {
  return useAuthQuery<RenderedInvitePreview>({
    queryKey: inviteTemplateKeys.preview(draft ?? { subject: "", body: "" }),
    queryFn: () => previewInviteTemplate(draft!),
    enabled: draft !== null,
    placeholderData: (previous) => previous,
  })
}

/** This client's invitation as it would be sent, link withheld. Fetched on
 * demand — opening the preview is a read of their contact details, and it
 * goes on the record — so `enabled` is the caller's "show me". */
export function useClientInvitePreview(
  patientId: string,
  versionIds: string[],
  enabled: boolean,
) {
  return useAuthQuery<ClientInvitePreview>({
    queryKey: inviteTemplateKeys.client(patientId, versionIds),
    queryFn: () => previewClientInvite(patientId, versionIds),
    enabled,
    retry: false,
    staleTime: 0,
  })
}
