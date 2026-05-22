// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * ChatPanelWithHistory — sidebar + active panel composer (THERAPY-fdcs).
 *
 * Drops the existing ``ChatPanel`` into a two-column layout with the
 * patient's conversation history on the left. The wrapper owns the
 * "which conversation is currently mounted" state and re-keys the
 * panel when the user picks a different row so internal state
 * (selection, messages) resets cleanly.
 *
 * Bumping ``listVersion`` after a new conversation lands or a
 * server-side mutation occurs lets the sidebar refresh without us
 * needing a global query cache here.
 */

import { useCallback, useState } from "react"

import { cn } from "@/lib/utils"
import type { ChatPanelProps } from "./ChatPanel"

import { ChatHistorySidebar } from "./ChatHistorySidebar"
import { ChatPanel } from "./ChatPanel"

export interface ChatPanelWithHistoryProps
  extends Omit<ChatPanelProps, "conversationId" | "onArchived"> {
  /** Optional initial conversation to open. */
  initialConversationId?: string
}

export function ChatPanelWithHistory({
  patientId,
  callerFeatureKey,
  callerSystemPrompt,
  defaultSourceSelection,
  title,
  className,
  initialConversationId,
}: ChatPanelWithHistoryProps) {
  const [activeConversationId, setActiveConversationId] = useState<
    string | null
  >(initialConversationId ?? null)
  // Re-mount key for the panel — bump to force a fresh "new chat" state
  // even when the previous active id was null.
  const [panelMountId, setPanelMountId] = useState(0)
  // Bumping this triggers the sidebar to re-fetch.
  const [listVersion, setListVersion] = useState(0)

  const handleSelect = useCallback((conversationId: string) => {
    setActiveConversationId(conversationId)
    setPanelMountId((n) => n + 1)
  }, [])

  const handleNew = useCallback(() => {
    setActiveConversationId(null)
    setPanelMountId((n) => n + 1)
  }, [])

  const handleArchived = useCallback((archivedId: string) => {
    // If the panel just archived its own conversation, drop selection
    // and bump the list so the row moves to the archived bucket.
    setActiveConversationId((current) =>
      current === archivedId ? null : current,
    )
    setListVersion((n) => n + 1)
  }, [])

  return (
    <div
      data-slot="chat-panel-with-history"
      className={cn(
        "flex h-full w-full gap-3",
        className,
      )}
    >
      <div className="flex-shrink-0 w-64 hidden md:flex">
        <ChatHistorySidebar
          patientId={patientId}
          callerFeatureKey={callerFeatureKey}
          activeConversationId={activeConversationId}
          onSelectConversation={handleSelect}
          onNewConversation={handleNew}
          refreshKey={listVersion}
          className="rounded-lg border border-neutral-200"
        />
      </div>
      <div className="flex-1 min-w-0">
        <ChatPanel
          // Re-mount the panel on selection changes so internal state
          // (selection, messages, manifest) resets cleanly. Without
          // this, switching conversations would replay a stale stream
          // against the new conversation id.
          key={`${activeConversationId ?? "new"}::${panelMountId}`}
          patientId={patientId}
          callerFeatureKey={callerFeatureKey}
          callerSystemPrompt={callerSystemPrompt}
          defaultSourceSelection={defaultSourceSelection}
          conversationId={activeConversationId ?? undefined}
          title={title}
          onArchived={handleArchived}
          // Notify the sidebar when the panel reports its own
          // conversation has been created so the list catches up
          // without a manual refresh.
          onConversationCreated={(id) => {
            setActiveConversationId(id)
            setListVersion((n) => n + 1)
          }}
        />
      </div>
    </div>
  )
}
