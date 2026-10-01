// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { formatInUserTimeZone, useUserTimeZone } from "@/hooks/usePreferences"
import type { InboxItemRendererProps } from "./itemRenderers"
import { ItemPanel, OpenLink, WHEN } from "./ItemPanel"

/** A note waiting for review and a signature. Signing it takes it off the list. */
export function NoteToSignItem({ item }: InboxItemRendererProps) {
  const timeZone = useUserTimeZone()
  const sessionDate = item.context.session_date
  return (
    <ItemPanel item={item}>
      {sessionDate && (
        <p className="text-sm text-neutral-800">
          Session on {formatInUserTimeZone(sessionDate, timeZone, WHEN)}
        </p>
      )}
      <OpenLink href={item.href} label="Review and sign" />
    </ItemPanel>
  )
}
