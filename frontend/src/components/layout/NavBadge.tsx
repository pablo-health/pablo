// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useUnreadThreadCount } from "@/hooks/useMessageInbox"
import type { NavItem } from "./sidebarExtensions"

/**
 * The count beside a nav label. Renders nothing at zero, while loading, or
 * where the count cannot be read (a deployment without the portal).
 */
export function NavBadge({ kind }: { kind: NonNullable<NavItem["badge"]> }) {
  const { data } = useUnreadThreadCount(kind === "unreadMessages")
  const count = data?.threads_with_unread ?? 0
  if (count === 0) return null
  return (
    <span
      className="ml-auto rounded-full bg-primary-600 px-2 py-0.5 text-xs font-semibold text-white"
      aria-label={`${count} unread`}
      data-testid="nav-badge-unread-messages"
    >
      {count}
    </span>
  )
}
