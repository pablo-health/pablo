// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { InboxItemRendererProps } from "./itemRenderers"
import { ItemPanel, OpenLink } from "./ItemPanel"

/** Any kind with no renderer of its own: what it is, and a way to it. */
export function GenericItem({ item }: InboxItemRendererProps) {
  return (
    <ItemPanel item={item}>
      {item.detail && <p className="whitespace-pre-wrap text-sm text-neutral-800">{item.detail}</p>}
      <OpenLink href={item.href} label="Open" />
    </ItemPanel>
  )
}
