// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice's portal invitation wording, and one client's invitation
 * previewed — against `backend/app/portal/invite_template_routes.py`.
 *
 * `editable` / `available` are false where the deployment's email channel
 * sends fixed wording. The screens read that as "offer nothing", because an
 * editor or a preview of text that is never sent would be untrue.
 */

import { del, get, post, put } from "./client"

export interface InvitePlaceholder {
  name: string
  label: string
  required: boolean
}

export interface InviteTemplate {
  editable: boolean
  subject: string
  body: string
  is_default: boolean
  placeholders: InvitePlaceholder[]
}

export interface InviteTemplateDraft {
  subject: string
  body: string
}

export interface RenderedInvitePreview {
  subject: string
  text: string
  /** Empty when the draft could be saved as it is. */
  problems: string[]
}

export interface ClientInvitePreview {
  available: boolean
  to_email: string | null
  subject: string | null
  text: string | null
}

const TEMPLATE = "/api/portal/invite-template"

export function getInviteTemplate(token?: string): Promise<InviteTemplate> {
  return get<InviteTemplate>(TEMPLATE, token)
}

export function saveInviteTemplate(
  draft: InviteTemplateDraft,
  token?: string,
): Promise<InviteTemplate> {
  return put<InviteTemplate>(TEMPLATE, draft, token)
}

export function resetInviteTemplate(token?: string): Promise<InviteTemplate> {
  return del<InviteTemplate>(TEMPLATE, token)
}

export function previewInviteTemplate(
  draft: InviteTemplateDraft,
  token?: string,
): Promise<RenderedInvitePreview> {
  return post<RenderedInvitePreview>(`${TEMPLATE}/preview`, draft, token)
}

export function previewClientInvite(
  patientId: string,
  versionIds: string[],
  token?: string,
): Promise<ClientInvitePreview> {
  return post<ClientInvitePreview>(
    `/api/patients/${patientId}/portal-invite/preview`,
    { version_ids: versionIds },
    token,
  )
}
