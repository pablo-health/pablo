// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  deleteBlankForm,
  finishBlankFormUpload,
  listBlankForms,
  sendBlankFormToStorage,
  startBlankFormUpload,
  type BlankForm,
} from "@/lib/api/intakeBlankForms"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** The blank forms a question may offer for download. */
export function useIntakeBlankForms(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.intakeBlankForms.list(),
    queryFn: (): Promise<BlankForm[]> => listBlankForms(token),
    staleTime: 60 * 1000,
  })
}

/**
 * Upload one, all three phases.
 *
 * One mutation rather than three, because the three only mean anything
 * together: a form whose bytes never landed appears in no list and can be
 * offered by no question, so there is nothing a caller would do between
 * them.
 */
export function useUploadIntakeBlankForm(token?: string) {
  return useAuthMutation({
    mutationFn: async ({ title, file }: { title: string; file: File }) => {
      const started = await startBlankFormUpload(
        {
          title,
          filename: file.name,
          mime_type: file.type,
          size_bytes: file.size,
        },
        token,
      )
      await sendBlankFormToStorage(started.upload, file)
      return finishBlankFormUpload(started.form_id, token)
    },
    invalidateKeys: [queryKeys.intakeBlankForms.all],
  })
}

/** Stop offering one. Never a hard delete — a question may still name it. */
export function useDeleteIntakeBlankForm(token?: string) {
  return useAuthMutation({
    mutationFn: (formId: string) => deleteBlankForm(formId, token),
    invalidateKeys: [queryKeys.intakeBlankForms.all],
  })
}
