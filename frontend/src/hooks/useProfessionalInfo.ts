// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { updateProfessionalInfo, type ProfessionalInfoUpdate } from "@/lib/api/users"
import { useAuthMutation } from "./useAuthQuery"

/** The user-status query the settings pages read the clinician's identifiers from. */
export const USER_STATUS_QUERY_KEY = ["user", "status"] as const

/** Save the clinician's own NPI and taxonomy code; the status query re-reads them. */
export function useUpdateProfessionalInfo(token?: string) {
  return useAuthMutation({
    mutationFn: (data: ProfessionalInfoUpdate) => updateProfessionalInfo(data, token),
    invalidateKeys: [USER_STATUS_QUERY_KEY],
  })
}
