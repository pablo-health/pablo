// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { InboxItemRendererProps } from "./itemRenderers"
import { ItemPanel, OpenLink } from "./ItemPanel"

/** A packet a client handed in. Accepting it on the chart takes it off the list. */
export function IntakeReviewItem({ item }: InboxItemRendererProps) {
  return (
    <ItemPanel item={item}>
      <OpenLink href={item.href} label="Review their answers" />
    </ItemPanel>
  )
}
