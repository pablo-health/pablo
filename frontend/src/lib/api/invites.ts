// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The invitee's side of a practice invitation. The link token goes in the
 * request body, never in a URL (see `backend/app/routes/practice_invites.py`).
 */

import { apiClient, post } from "./client"

export interface InvitePreview {
  practice_name: string
  email: string
}

export interface RedeemedInvite {
  practice_id: string
  practice_name: string
}

/** Which practice an open invitation is for. Needs no sign-in. */
export function previewInvite(token: string): Promise<InvitePreview> {
  return apiClient<InvitePreview>("/api/invites/preview", {
    method: "POST",
    body: JSON.stringify({ token }),
    auth: "none",
  })
}

/** Join the signed-in account to the invitation's practice. */
export function redeemInvite(token: string, confirmMismatch: boolean): Promise<RedeemedInvite> {
  return post<RedeemedInvite>("/api/invites/redeem", {
    token,
    confirm_mismatch: confirmMismatch,
  })
}
