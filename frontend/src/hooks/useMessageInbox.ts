// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  closeThread,
  getThread,
  markThreadRead,
  reopenThread,
  replyToThread,
  type ReplyMessage,
  type ThreadDetail,
} from "@/lib/api/messageInbox"
import { inboxKeys } from "./inboxKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export const messageInboxKeys = {
  all: ["messageInbox"] as const,
  thread: (threadId: string) => [...messageInboxKeys.all, "thread", threadId] as const,
}

/** How often an open conversation looks for new messages. */
const POLL_MS = 60_000

export function useThread(threadId: string | null) {
  return useAuthQuery<ThreadDetail>({
    queryKey: messageInboxKeys.thread(threadId ?? ""),
    queryFn: () => getThread(threadId as string),
    enabled: threadId !== null,
    // An open conversation picks up what arrives while it is open.
    refetchInterval: POLL_MS,
  })
}

/** Every write re-reads the conversation and the Inbox: what is open moves. */
const EVERYTHING = [messageInboxKeys.all, inboxKeys.all]

/**
 * Reply into a thread. `inReplyToMessageId` names the client message being
 * answered, so the Inbox resolves that one and no other.
 */
export function useReplyToThread(threadId: string, inReplyToMessageId?: string) {
  return useAuthMutation<ReplyMessage, string>({
    mutationFn: (body) => replyToThread(threadId, body, inReplyToMessageId),
    invalidateKeys: EVERYTHING,
  })
}

export function useMarkThreadRead() {
  return useAuthMutation<unknown, string>({
    mutationFn: (threadId) => markThreadRead(threadId),
    invalidateKeys: EVERYTHING,
  })
}

export function useCloseThread(threadId: string) {
  return useAuthMutation<unknown, void>({
    mutationFn: () => closeThread(threadId),
    invalidateKeys: EVERYTHING,
  })
}

export function useReopenThread(threadId: string) {
  return useAuthMutation<unknown, void>({
    mutationFn: () => reopenThread(threadId),
    invalidateKeys: EVERYTHING,
  })
}
