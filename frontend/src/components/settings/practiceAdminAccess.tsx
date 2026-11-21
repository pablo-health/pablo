// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useSettingsUserStatus } from "./useSettingsPreferences"

export const PRACTICE_ADMIN_ONLY = "Only the practice owner or a practice admin can change this."

/**
 * Whether the signed-in clinician may change a practice-wide setting.
 *
 * The owner or a practice admin may (`is_practice_admin` on /me/status, the
 * same test the write routes make). Everyone else sees the setting read-only.
 *
 * `canEdit` is false while the status is still loading, so nobody types into
 * a form the server would refuse. `readOnly` is true only once the status has
 * arrived and said no, so the note never flashes up for an owner whose status
 * has not loaded yet.
 */
export function usePracticeAdminAccess(): { canEdit: boolean; readOnly: boolean } {
  const { data } = useSettingsUserStatus()
  const canEdit = data?.is_practice_admin === true
  return { canEdit, readOnly: data !== undefined && !canEdit }
}

/** The one line shown on a practice-wide setting the clinician cannot change. */
export function PracticeAdminOnlyNote() {
  return (
    <p className="text-[12.5px] text-muted-foreground" data-testid="practice-admin-only">
      {PRACTICE_ADMIN_ONLY}
    </p>
  )
}
