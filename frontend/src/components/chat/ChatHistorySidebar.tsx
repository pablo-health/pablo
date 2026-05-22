// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Chat history sidebar (THERAPY-fdcs).
 *
 * Lists every chat conversation the caller has for a given patient,
 * with open / archive / rename / delete affordances. Sits beside the
 * ChatPanel in :class:`ChatPanelWithHistory` so a clinician can browse
 * prior chats without leaving the chart.
 *
 * Why this is a sibling of ChatPanel and not nested inside it: the
 * panel owns one active conversation; the sidebar owns the *index*.
 * Keeping them separate lets the parent decide which conversation to
 * mount, and avoids forcing every ChatPanel mount to also fetch a
 * conversation list.
 */

import { useCallback, useEffect, useState } from "react"
import {
  Archive,
  Loader2,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react"

import { cn } from "@/lib/utils"
import {
  deleteConversation,
  listConversations,
  updateConversation,
} from "@/lib/chat/api"
import type { ChatConversation } from "@/lib/chat/types"

export interface ChatHistorySidebarProps {
  patientId: string
  callerFeatureKey?: string
  /** Currently-open conversation in the parent panel (highlighted). */
  activeConversationId?: string | null
  /** Called when the user picks a different conversation from the list. */
  onSelectConversation: (conversationId: string) => void
  /** Called when the user clicks "New chat". */
  onNewConversation: () => void
  /**
   * Bumping ``refreshKey`` (e.g. when a brand-new conversation was just
   * created in the panel) forces the sidebar to re-fetch its list.
   */
  refreshKey?: number
  className?: string
}

interface InlineRenameState {
  conversationId: string
  draft: string
}

export function ChatHistorySidebar({
  patientId,
  callerFeatureKey,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  refreshKey,
  className,
}: ChatHistorySidebarProps) {
  const [conversations, setConversations] = useState<ChatConversation[]>([])
  const [showArchived, setShowArchived] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [rename, setRename] = useState<InlineRenameState | null>(null)
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const resp = await listConversations({
        patientId,
        callerFeatureKey,
        includeArchived: showArchived,
      })
      // Backend already sorts by last_turn_at desc nulls last; defensive
      // client-side sort keeps the order stable if a future server
      // change relaxes that.
      const sorted = [...resp.data].sort((a, b) => {
        const at = a.last_turn_at ?? a.created_at
        const bt = b.last_turn_at ?? b.created_at
        return bt.localeCompare(at)
      })
      setConversations(sorted)
    } catch (exc) {
      setLoadError(
        exc instanceof Error ? exc.message : "Failed to load conversations.",
      )
    } finally {
      setLoading(false)
    }
  }, [patientId, callerFeatureKey, showArchived])

  useEffect(() => {
    void refresh()
  }, [refresh, refreshKey])

  const handleArchive = useCallback(
    async (conv: ChatConversation) => {
      setBusyId(conv.id)
      try {
        await updateConversation(conv.id, { archive: true })
        await refresh()
      } finally {
        setBusyId(null)
      }
    },
    [refresh],
  )

  const handleUnarchive = useCallback(
    async (conv: ChatConversation) => {
      setBusyId(conv.id)
      try {
        await updateConversation(conv.id, { archive: false })
        await refresh()
      } finally {
        setBusyId(null)
      }
    },
    [refresh],
  )

  const handleDelete = useCallback(
    async (conv: ChatConversation) => {
      setBusyId(conv.id)
      try {
        await deleteConversation(conv.id, "purge")
        setPendingDelete(null)
        await refresh()
      } finally {
        setBusyId(null)
      }
    },
    [refresh],
  )

  const handleSubmitRename = useCallback(
    async (conv: ChatConversation, draft: string) => {
      const trimmed = draft.trim()
      if (!trimmed || trimmed === conv.title) {
        setRename(null)
        return
      }
      setBusyId(conv.id)
      try {
        await updateConversation(conv.id, { title: trimmed })
        setRename(null)
        await refresh()
      } finally {
        setBusyId(null)
      }
    },
    [refresh],
  )

  return (
    <aside
      data-slot="chat-history-sidebar"
      className={cn(
        "flex h-full w-full flex-col gap-2 border-r border-neutral-200 bg-neutral-50/60 p-3",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-display text-sm font-semibold text-neutral-900">
          Chat history
        </h3>
        <button
          type="button"
          onClick={onNewConversation}
          className="inline-flex items-center gap-1 rounded-md border border-neutral-300 bg-white px-2 py-1 text-xs font-medium text-neutral-700 hover:bg-neutral-100"
          data-testid="chat-history-new"
        >
          <Plus className="size-3" /> New
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto rounded-md border border-neutral-200 bg-white">
        {loading ? (
          <div className="flex items-center justify-center p-4 text-xs text-neutral-500">
            <Loader2 className="size-3 animate-spin mr-1" /> Loading…
          </div>
        ) : loadError ? (
          <div className="p-3 text-xs text-red-600">{loadError}</div>
        ) : conversations.length === 0 ? (
          <div className="p-3 text-xs text-neutral-500">
            {showArchived
              ? "No archived conversations."
              : "No conversations yet. Start one with “New”."}
          </div>
        ) : (
          <ul role="list" className="divide-y divide-neutral-100">
            {conversations.map((conv) => {
              const isActive = conv.id === activeConversationId
              const isRenaming = rename?.conversationId === conv.id
              const isPendingDelete = pendingDelete === conv.id
              const isArchived = conv.archived_at !== null
              const ts = conv.last_turn_at ?? conv.created_at
              return (
                <li
                  key={conv.id}
                  data-testid="chat-history-row"
                  data-conversation-id={conv.id}
                  className={cn(
                    "group flex flex-col gap-1 px-2 py-2 text-sm",
                    isActive && "bg-primary-50",
                  )}
                >
                  {isRenaming ? (
                    <form
                      onSubmit={(e) => {
                        e.preventDefault()
                        void handleSubmitRename(conv, rename.draft)
                      }}
                      className="flex items-center gap-1"
                    >
                      <input
                        autoFocus
                        type="text"
                        value={rename.draft}
                        onChange={(e) =>
                          setRename({
                            conversationId: conv.id,
                            draft: e.target.value,
                          })
                        }
                        onBlur={() =>
                          void handleSubmitRename(conv, rename.draft)
                        }
                        onKeyDown={(e) => {
                          if (e.key === "Escape") setRename(null)
                        }}
                        className="flex-1 rounded border border-neutral-300 px-2 py-1 text-sm"
                        maxLength={200}
                      />
                    </form>
                  ) : (
                    <button
                      type="button"
                      onClick={() => onSelectConversation(conv.id)}
                      className="text-left font-medium text-neutral-900 hover:text-primary-700"
                      data-testid="chat-history-open"
                    >
                      <span className="truncate block">{conv.title}</span>
                    </button>
                  )}

                  <div className="flex items-center justify-between gap-2 text-xs text-neutral-500">
                    <time
                      dateTime={ts}
                      title={new Date(ts).toLocaleString()}
                      className="truncate"
                    >
                      {formatRelative(ts)}
                      {isArchived ? " · archived" : ""}
                    </time>
                    <div className="flex items-center opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
                      <RowAction
                        title="Rename"
                        onClick={() =>
                          setRename({
                            conversationId: conv.id,
                            draft: conv.title,
                          })
                        }
                        disabled={busyId === conv.id}
                      >
                        <Pencil className="size-3" />
                      </RowAction>
                      {isArchived ? (
                        <RowAction
                          title="Restore"
                          onClick={() => void handleUnarchive(conv)}
                          disabled={busyId === conv.id}
                        >
                          <Archive className="size-3" />
                        </RowAction>
                      ) : (
                        <RowAction
                          title="Archive"
                          onClick={() => void handleArchive(conv)}
                          disabled={busyId === conv.id}
                          data-testid="chat-history-archive"
                        >
                          <Archive className="size-3" />
                        </RowAction>
                      )}
                      <RowAction
                        title="Delete"
                        onClick={() => setPendingDelete(conv.id)}
                        disabled={busyId === conv.id}
                        destructive
                        data-testid="chat-history-delete"
                      >
                        <Trash2 className="size-3" />
                      </RowAction>
                    </div>
                  </div>

                  {isPendingDelete ? (
                    <div
                      role="alertdialog"
                      className="mt-1 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-900"
                      data-testid="chat-history-delete-confirm"
                    >
                      <p className="mb-2">
                        Delete this conversation? This permanently removes
                        every message in it — there is no undo.
                      </p>
                      <div className="flex justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => setPendingDelete(null)}
                          className="rounded border border-neutral-300 bg-white px-2 py-1 hover:bg-neutral-100"
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleDelete(conv)}
                          className="rounded bg-red-600 px-2 py-1 font-medium text-white hover:bg-red-700"
                          disabled={busyId === conv.id}
                          data-testid="chat-history-delete-confirm-button"
                        >
                          {busyId === conv.id ? "Deleting…" : "Delete"}
                        </button>
                      </div>
                    </div>
                  ) : null}
                </li>
              )
            })}
          </ul>
        )}
      </div>

      <label className="flex items-center gap-2 text-xs text-neutral-600">
        <input
          type="checkbox"
          checked={showArchived}
          onChange={(e) => setShowArchived(e.target.checked)}
          className="size-3"
          data-testid="chat-history-show-archived"
        />
        Show archived
      </label>
    </aside>
  )
}

interface RowActionProps {
  title: string
  onClick: () => void
  disabled?: boolean
  destructive?: boolean
  children: React.ReactNode
  "data-testid"?: string
}

function RowAction({
  title,
  onClick,
  disabled,
  destructive,
  children,
  ...rest
}: RowActionProps) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "rounded p-1 hover:bg-neutral-100 disabled:opacity-40",
        destructive && "text-red-700 hover:bg-red-50",
      )}
      data-testid={rest["data-testid"]}
    >
      {children}
    </button>
  )
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function formatRelative(iso: string): string {
  const ts = new Date(iso).getTime()
  if (Number.isNaN(ts)) return ""
  const diffMs = Date.now() - ts
  const minute = 60_000
  const hour = 60 * minute
  const day = 24 * hour
  if (diffMs < minute) return "just now"
  if (diffMs < hour) return `${Math.floor(diffMs / minute)}m ago`
  if (diffMs < day) return `${Math.floor(diffMs / hour)}h ago`
  if (diffMs < 7 * day) return `${Math.floor(diffMs / day)}d ago`
  return new Date(iso).toLocaleDateString()
}
