// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  getEmailSender,
  saveEmailSender,
  type EmailSender,
  type EmailSenderFields,
} from "@/lib/api/emailSender"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export const emailSenderKeys = {
  all: ["emailSender"] as const,
}

/** Who the practice's client email is from. `retry: false`: a deployment
 * without the portal answers 404, and asking again does not change that. */
export function useEmailSender() {
  return useAuthQuery<EmailSender>({
    queryKey: emailSenderKeys.all,
    queryFn: () => getEmailSender(),
    retry: false,
  })
}

export function useSaveEmailSender() {
  return useAuthMutation<EmailSender, EmailSenderFields>({
    mutationFn: (fields) => saveEmailSender(fields),
    invalidateKeys: [emailSenderKeys.all],
  })
}
