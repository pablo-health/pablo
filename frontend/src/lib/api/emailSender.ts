// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Who the practice's email to its clients is from, and where replies go —
 * against `backend/app/portal/sender_routes.py`.
 *
 * `applies` is false where the deployment's email channel sends under its own
 * name; the screen then offers nothing, because a preview of a From line no
 * client will see would be untrue.
 */

import { get, put } from "./client"

/** The three settings. `null` means the default for that field. */
export interface EmailSenderFields {
  sender_name: string | null
  sender_local_part: string | null
  reply_to: string | null
}

export interface EmailSender {
  can_edit: boolean
  applies: boolean
  chosen: EmailSenderFields
  defaults: {
    sender_name: string
    sender_local_part: string
    reply_to: string | null
  }
  /** The practice's domain mail leaves from, or null while none can send. */
  sending_domain: string | null
  /** The deployment's own sending address, when it is known. */
  deployment_from_address: string | null
  effective: {
    from_name: string
    from_address: string | null
    reply_to: string | null
  }
}

const EMAIL_SENDER = "/api/practice/email-sender"

export function getEmailSender(token?: string): Promise<EmailSender> {
  return get<EmailSender>(EMAIL_SENDER, token)
}

export function saveEmailSender(fields: EmailSenderFields, token?: string): Promise<EmailSender> {
  return put<EmailSender>(EMAIL_SENDER, fields, token)
}
