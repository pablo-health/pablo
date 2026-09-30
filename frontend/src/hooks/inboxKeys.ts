// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { InboxView } from "@/lib/api/inbox"

/**
 * The Inbox's query keys, in a file of their own so the messaging hooks can
 * invalidate the Inbox after a reply without importing the Inbox hooks.
 */
export const inboxKeys = {
  all: ["inbox"] as const,
  list: (view: InboxView, kinds: readonly string[]) => [...inboxKeys.all, "list", view, ...kinds] as const,
  count: () => [...inboxKeys.all, "count"] as const,
}
