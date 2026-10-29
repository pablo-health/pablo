// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PracticeAdminsCard
 *
 * The practice owner choosing which colleagues may change practice-wide
 * settings with them. Rendered only for the owner; the server refuses anyone
 * else (`/api/practice/admins` is the owner's alone), and an admin cannot
 * choose admins.
 *
 * The owner is not listed: the owner may always change settings and cannot be
 * granted or revoked, so a row for them would be a switch that does nothing.
 */

"use client"

import { SavedIndicator, SettingsCard, SettingsRow, Toggle, useSavedFlash } from "@/components/settings/ui"
import { usePracticeAdmins, useSetPracticeAdmin } from "@/hooks/usePracticeAdmins"

export function PracticeAdminsCard() {
  const { data } = usePracticeAdmins(true)
  const update = useSetPracticeAdmin()
  const { saved, flashSaved } = useSavedFlash()
  const colleagues = data?.clinicians.filter((c) => !c.is_owner) ?? []

  return (
    <SettingsCard
      title="Practice admins"
      description="Admins can change your practice's settings. Only you choose who is an admin."
      flush
    >
      {data && colleagues.length === 0 && (
        <p className="px-[22px] pb-5 pt-3.5 text-[12.5px] text-muted-foreground">
          When another clinician joins your practice, you can make them an admin here.
        </p>
      )}
      {colleagues.map((clinician) => {
        const name = clinician.name?.trim() || clinician.email
        const label = `${name} is an admin`
        return (
          <SettingsRow
            key={clinician.user_id}
            label={name}
            description={name === clinician.email ? undefined : clinician.email}
          >
            {update.variables?.userId === clinician.user_id && <SavedIndicator saved={saved} />}
            <Toggle
              label={label}
              checked={clinician.is_admin}
              disabled={update.isPending}
              onChange={(next) =>
                update.mutate(
                  { userId: clinician.user_id, isAdmin: next },
                  { onSuccess: flashSaved },
                )
              }
            />
          </SettingsRow>
        )
      })}
      {update.isError && (
        <p role="alert" className="px-[22px] pb-3 text-sm text-red-600">
          That didn&apos;t save. Try again.
        </p>
      )}
    </SettingsCard>
  )
}
