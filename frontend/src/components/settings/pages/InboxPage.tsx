// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import type { UserPreferences } from "@/lib/api/users"
import { SegmentedControl, SettingsCard, type SegmentedOption } from "../ui"
import { useSettingsPreferences } from "../useSettingsPreferences"

type EarlierMessages = NonNullable<UserPreferences["inbox_reply_earlier_messages"]>

const OPTIONS: SegmentedOption<EarlierMessages>[] = [
  { value: "ask", label: "Ask me" },
  { value: "always", label: "Mark handled too" },
  { value: "never", label: "Leave open" },
]

/** You > Inbox. The answer the Inbox remembers when asked "Always" or "Don't ask again". */
export function InboxPage() {
  const { preferences, save } = useSettingsPreferences()
  const people = usePeopleTerm()
  if (!preferences) return null
  const label = `When I reply to a ${people.one}, their earlier unanswered messages:`

  return (
    <SettingsCard title={`Replying to ${people.many}`}>
      <p className="mb-3 text-sm text-foreground">
        {label}
      </p>
      <SegmentedControl
        label={label}
        value={preferences.inbox_reply_earlier_messages ?? "ask"}
        options={OPTIONS}
        onChange={(next) => save({ ...preferences, inbox_reply_earlier_messages: next })}
      />
    </SettingsCard>
  )
}
