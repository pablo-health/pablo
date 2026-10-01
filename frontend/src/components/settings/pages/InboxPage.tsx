// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { UserPreferences } from "@/lib/api/users"
import { SegmentedControl, SettingsCard, type SegmentedOption } from "../ui"
import { useSettingsPreferences } from "../useSettingsPreferences"

type EarlierMessages = NonNullable<UserPreferences["inbox_reply_earlier_messages"]>

const LABEL = "When I reply to a client, their earlier unanswered messages:"

const OPTIONS: SegmentedOption<EarlierMessages>[] = [
  { value: "ask", label: "Ask me" },
  { value: "always", label: "Mark handled too" },
  { value: "never", label: "Leave open" },
]

/** You > Inbox. The answer the Inbox remembers when asked "Always" or "Don't ask again". */
export function InboxPage() {
  const { preferences, save } = useSettingsPreferences()
  if (!preferences) return null

  return (
    <SettingsCard title="Replying to clients">
      <p className="mb-3 text-sm text-foreground">
        {LABEL}
      </p>
      <SegmentedControl
        label={LABEL}
        value={preferences.inbox_reply_earlier_messages ?? "ask"}
        options={OPTIONS}
        onChange={(next) => save({ ...preferences, inbox_reply_earlier_messages: next })}
      />
    </SettingsCard>
  )
}
