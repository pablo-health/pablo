// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  dismissInboxItem,
  getInboxCount,
  handleEarlierMessages,
  listInbox,
  restoreInboxItem,
  snoozeInboxItem,
  type HandledEarlier,
  type InboxItem,
  type InboxList,
  type InboxView,
} from "@/lib/api/inbox"
import { inboxKeys } from "./inboxKeys"
import { messageInboxKeys } from "./useMessageInbox"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** How often the list and the badge look for new items. */
const POLL_MS = 60_000

/** Every write re-reads the Inbox and the conversations behind it. */
const EVERYTHING = [inboxKeys.all, messageInboxKeys.all]

export function useInboxItems(view: InboxView, kinds: readonly string[]) {
  return useAuthQuery<InboxList>({
    queryKey: inboxKeys.list(view, kinds),
    queryFn: () => listInbox(view, [...kinds]),
    refetchInterval: POLL_MS,
  })
}

/** The nav badge. `retry: false`: a failed count shows no badge rather than a retry storm. */
export function useInboxCount() {
  return useAuthQuery<{ count: number }>({
    queryKey: inboxKeys.count(),
    queryFn: () => getInboxCount(),
    refetchInterval: POLL_MS,
    retry: false,
  })
}

interface ItemRef {
  kind: string
  sourceId: string
}

export function useDismissInboxItem() {
  return useAuthMutation<InboxItem, ItemRef>({
    mutationFn: ({ kind, sourceId }) => dismissInboxItem(kind, sourceId),
    invalidateKeys: EVERYTHING,
  })
}

export function useSnoozeInboxItem() {
  return useAuthMutation<InboxItem, ItemRef & { until: Date }>({
    mutationFn: ({ kind, sourceId, until }) => snoozeInboxItem(kind, sourceId, until),
    invalidateKeys: EVERYTHING,
  })
}

export function useRestoreInboxItems() {
  return useAuthMutation<InboxItem[], ItemRef[]>({
    mutationFn: (items) =>
      Promise.all(items.map(({ kind, sourceId }) => restoreInboxItem(kind, sourceId))),
    invalidateKeys: EVERYTHING,
  })
}

export function useHandleEarlierMessages() {
  return useAuthMutation<HandledEarlier, string>({
    mutationFn: (messageId) => handleEarlierMessages(messageId),
    invalidateKeys: EVERYTHING,
  })
}
