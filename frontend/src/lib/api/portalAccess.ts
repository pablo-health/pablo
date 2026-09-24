// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Per-patient portal access: invite, state, revoke.
 *
 * The clinician half of the magic-link invitation, against the routes in
 * `backend/app/portal/routes.py`. Issuing mints the invite and hands it to
 * whichever delivery adapters the deployment supplies; the state read reports
 * whether one is outstanding and how many sessions are live; the revoke is
 * the kill switch.
 *
 * The invite response deliberately carries NO credential — not the token, not
 * the link, not the step-up code. Those go to the patient's own email and
 * phone and nowhere else, so that a clinician's screenshot is not a
 * credential. `PortalInviteAccepted` is the whole shape the route returns,
 * and it is this narrow on purpose.
 *
 * A deployment with no portal answers 404 here, which the card reads as
 * "render nothing" rather than as an error worth a sentence.
 */

import { del, get, post } from "./client"

/** What the server says after minting an invite. No credential, by design. */
export interface PortalInviteAccepted {
  patient_id: string
  /** Unix seconds — when the magic link stops redeeming. */
  invite_expires_at: number
}

export interface PortalAccessState {
  patient_id: string
  invite_outstanding: boolean
  live_sessions: number
  /** Unix seconds — when access was last cut off, or null if it never was. */
  revoked_at: number | null
}

export interface PortalAccessRevoked {
  patient_id: string
  sessions_revoked: number
  invites_revoked: number
}

function invitePath(patientId: string): string {
  return `/api/patients/${patientId}/portal-invite`
}

function accessPath(patientId: string): string {
  return `/api/patients/${patientId}/portal-access`
}

export async function getPortalAccess(
  patientId: string,
  token?: string,
): Promise<PortalAccessState> {
  return get<PortalAccessState>(accessPath(patientId), token)
}

export async function issuePortalInvite(
  patientId: string,
  token?: string,
): Promise<PortalInviteAccepted> {
  return post<PortalInviteAccepted>(invitePath(patientId), {}, token)
}

export async function revokePortalAccess(
  patientId: string,
  token?: string,
): Promise<PortalAccessRevoked> {
  return del<PortalAccessRevoked>(accessPath(patientId), token)
}
