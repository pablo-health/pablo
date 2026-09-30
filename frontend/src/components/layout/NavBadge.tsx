// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useInboxCount } from "@/hooks/useInbox"
import type { NavItem } from "./sidebarExtensions"

/**
 * The count beside a nav label. Renders nothing at zero, while loading, or
 * where the count cannot be read. There is one: the Inbox's open items.
 */
export function NavBadge({ kind }: { kind: NonNullable<NavItem["badge"]> }) {
  const { data } = useInboxCount()
  const count = kind === "inbox" ? (data?.count ?? 0) : 0
  if (count === 0) return null
  return (
    <span
      className="ml-auto rounded-full bg-primary-600 px-2 py-0.5 text-xs font-semibold text-white"
      aria-label={`${count} open`}
      data-testid="nav-badge-inbox"
    >
      {count}
    </span>
  )
}
