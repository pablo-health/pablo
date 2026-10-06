// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * AiNotesConsentCard
 *
 * Whether the practice asks each client to agree to AI-assisted notes. On by
 * default. On, Today offers a script to read before recording and each session
 * note shows the client's answer. Off hides both; the answer stays on the
 * chart either way, which is why the description does not mention it.
 *
 * Shown whatever the audio-retention flag says: the script reads the stored
 * retention window, not the retention control.
 */

"use client"

import { SavedIndicator, SettingsCard, SettingsRow, Toggle, useSavedFlash } from "@/components/settings/ui"
import { useAiNotesConsentSetting, useUpdateAiNotesConsentSetting } from "@/hooks/useAiConsent"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"

export function AiNotesConsentCard() {
  const { data: setting } = useAiNotesConsentSetting()
  const update = useUpdateAiNotesConsentSetting()
  const people = usePeopleTerm()
  const { saved, flashSaved } = useSavedFlash()
  const label = `Ask ${people.many} to agree to AI-assisted notes`

  return (
    <SettingsCard title="AI-assisted notes" flush>
      <SettingsRow
        label={label}
        description={
          setting && !setting.can_change
            ? "Only the practice owner can change this."
            : `Adds a script to read before recording, and the ${people.one}'s answer on each session note.`
        }
      >
        <SavedIndicator saved={saved} />
        <Toggle
          label={label}
          checked={setting?.ask_clients_about_ai_notes ?? true}
          disabled={!setting || !setting.can_change || update.isPending}
          onChange={(next) => update.mutate(next, { onSuccess: flashSaved })}
        />
      </SettingsRow>
      {update.isError && (
        <p role="alert" className="px-[22px] pb-3 text-sm text-red-600">
          That didn&apos;t save. Try again.
        </p>
      )}
    </SettingsCard>
  )
}
