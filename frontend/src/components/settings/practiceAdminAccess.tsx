// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useSettingsUserStatus } from "./useSettingsPreferences"

export const PRACTICE_ADMIN_ONLY = "Only the practice owner or a practice admin can change this."
export const PRACTICE_ADMIN_STATUS_UNAVAILABLE =
  "Could not check who can change this. Reload to try again."

/**
 * Which line, if any, a practice-wide setting shows above its controls.
 *
 * - `"admin-only"`: the status arrived and the clinician is not the owner or
 *   an admin.
 * - `"status-unavailable"`: the status request failed, so we do not know. The
 *   line must not claim the clinician lacks permission; they may be the owner.
 */
export type PracticeAdminNote = "admin-only" | "status-unavailable"

/**
 * Whether the signed-in clinician may change a practice-wide setting.
 *
 * The owner or a practice admin may (`is_practice_admin` on /me/status, the
 * same test the write routes make). Everyone else sees the setting read-only.
 *
 * `canEdit` is false until the status says yes, so nobody types into a form
 * the server would refuse: while it loads, and when it fails. `note` is null
 * while loading, so no line flashes up for an owner whose status has not
 * arrived. A failed request with no earlier answer gets the neutral
 * could-not-check line; a failed refetch keeps trusting the answer it had.
 */
export function usePracticeAdminAccess(): { canEdit: boolean; note: PracticeAdminNote | null } {
  const { data, isError } = useSettingsUserStatus()
  const canEdit = data?.is_practice_admin === true
  let note: PracticeAdminNote | null = null
  if (data !== undefined) {
    note = canEdit ? null : "admin-only"
  } else if (isError) {
    note = "status-unavailable"
  }
  return { canEdit, note }
}

/** The one line shown on a practice-wide setting the clinician cannot change. */
export function PracticeAdminAccessNote({ note }: { note: PracticeAdminNote }) {
  if (note === "status-unavailable") {
    return (
      <p
        className="text-[12.5px] text-muted-foreground"
        data-testid="practice-admin-status-unavailable"
      >
        {PRACTICE_ADMIN_STATUS_UNAVAILABLE}
      </p>
    )
  }
  return (
    <p className="text-[12.5px] text-muted-foreground" data-testid="practice-admin-only">
      {PRACTICE_ADMIN_ONLY}
    </p>
  )
}
