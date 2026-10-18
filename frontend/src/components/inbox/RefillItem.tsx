// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { DueBadge } from "@/components/refills/DueBadge"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { useUserTimeZone } from "@/hooks/usePreferences"
import type { InboxItemRendererProps } from "./itemRenderers"
import { ItemPanel, OpenLink } from "./ItemPanel"

/** A refill request. It is answered on the Refills page, which takes it off the list. */
export function RefillItem({ item }: InboxItemRendererProps) {
  const people = usePeopleTerm()
  const timeZone = useUserTimeZone()
  return (
    <ItemPanel item={item}>
      {item.due_at && (
        <p className="text-sm">
          <DueBadge dueAt={item.due_at} timeZone={timeZone} />
        </p>
      )}
      {item.detail && (
        <p className="whitespace-pre-wrap text-sm text-neutral-800">
          <span className="font-medium">Note from {people.one}:</span> {item.detail}
        </p>
      )}
      <OpenLink href={item.href} label="Answer on the Refills page" />
    </ItemPanel>
  )
}
