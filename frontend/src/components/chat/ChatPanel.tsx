// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * ChatPanel — Phase 4 baseline (THERAPY-q3z).
 *
 * Implements §13.1 prop API, §13.2 source chip rail, §13.3 per-message
 * manifest disclosure, §13.8 error states, §13.9 bubbles, §13.10
 * composer, §13.11 archive, §13.12 SSE consumer.
 *
 * Out of scope for this bead (follow-on beads 4c / 4d):
 * - §13.4 briefing card
 * - §13.5 caller-supplied starter prompts
 * - §13.6 view-system-prompt chevron
 * - §13.7 scope/safety footer
 * - NODE_ENV-gated /dev/chat mount route
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { cn } from "@/lib/utils"
import {
  createConversation,
  getConversation,
  updateConversation,
} from "@/lib/chat/api"
import { streamChatMessages } from "@/lib/chat/sse"
import {
  type ChatErrorCode,
  type ChatMessage,
  type ContextManifest,
  type SourceKey,
  type SourceSelection,
} from "@/lib/chat/types"

import { ArchiveButton } from "./ArchiveButton"
import { ChatErrorNotice } from "./ChatErrorNotice"
import { Composer } from "./Composer"
import { MessageBubble } from "./MessageBubble"
import { SourceChipDetail } from "./SourceChipDetail"
import { SourceChipRail } from "./SourceChipRail"

const DEFAULT_TOKEN_BUDGET = 600_000

// ---------------------------------------------------------------------------
// Public prop API (§13.1)
// ---------------------------------------------------------------------------

export interface ChatPanelProps {
  patientId: string
  callerFeatureKey: string
  callerSystemPrompt: string
  defaultSourceSelection?: SourceSelection
  conversationId?: string
  title?: string
  className?: string
  onArchived?: (conversationId: string) => void
}

// ---------------------------------------------------------------------------
// Internal state types
// ---------------------------------------------------------------------------

interface PendingErrorState {
  code: ChatErrorCode | string
  serverMessage?: string
  /** True if "Retry" should be offered. */
  retryable: boolean
}

interface SendCacheEntry {
  content: string
  selection: SourceSelection
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ChatPanel({
  patientId,
  callerFeatureKey,
  callerSystemPrompt,
  defaultSourceSelection,
  conversationId: initialConversationId,
  title,
  className,
  onArchived,
}: ChatPanelProps) {
  const [conversationId, setConversationId] = useState<string | null>(
    initialConversationId ?? null,
  )
  const [conversationTitle, setConversationTitle] = useState<string>(
    title ?? "",
  )
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [serverDefault, setServerDefault] = useState<SourceSelection>(
    defaultSourceSelection ?? {},
  )
  const [selection, setSelection] = useState<SourceSelection>(
    defaultSourceSelection ?? {},
  )
  const [latestManifest, setLatestManifest] = useState<ContextManifest | null>(null)
  const [archived, setArchived] = useState(false)
  const [streamingAssistantId, setStreamingAssistantId] = useState<string | null>(null)
  const [error, setError] = useState<PendingErrorState | null>(null)
  const [hydrating, setHydrating] = useState<boolean>(Boolean(initialConversationId))
  const [detailFor, setDetailFor] = useState<SourceKey | null>(null)

  const lastSendRef = useRef<SendCacheEntry | null>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)

  const tokenBudget = latestManifest?.token_budget ?? DEFAULT_TOKEN_BUDGET
  const contextTokens = latestManifest?.total_tokens_est ?? 0
  const composerDisabled = archived || streamingAssistantId !== null

  // -------------------------------------------------------------------------
  // Hydrate when an existing conversationId is provided
  // -------------------------------------------------------------------------

  useEffect(() => {
    if (!initialConversationId) {
      return
    }
    let cancelled = false
    getConversation(initialConversationId)
      .then((detail) => {
        if (cancelled) return
        setConversationId(detail.id)
        setConversationTitle(detail.title)
        setMessages(detail.messages)
        setServerDefault(detail.default_source_selection ?? {})
        setSelection(detail.default_source_selection ?? {})
        setArchived(detail.archived_at !== null)
        const lastAssistantManifest = [...detail.messages]
          .reverse()
          .find((m) => m.role === "user" && m.context_manifest)?.context_manifest
        if (lastAssistantManifest) {
          setLatestManifest(lastAssistantManifest)
        }
      })
      .catch((exc: unknown) => {
        if (cancelled) return
        const code =
          exc instanceof Error && exc.name === "ApiError"
            ? "llm_error"
            : "llm_error"
        setError({ code, serverMessage: (exc as Error).message, retryable: false })
      })
      .finally(() => {
        if (!cancelled) setHydrating(false)
      })
    return () => {
      cancelled = true
    }
  }, [initialConversationId])

  // -------------------------------------------------------------------------
  // Auto-scroll on new content
  // -------------------------------------------------------------------------

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
  }, [messages, streamingAssistantId])

  // -------------------------------------------------------------------------
  // Send
  // -------------------------------------------------------------------------

  const performSend = useCallback(
    async (content: string, sel: SourceSelection) => {
      setError(null)
      lastSendRef.current = { content, selection: sel }

      // Optimistic user bubble. ``meta`` will replace the id once the
      // backend assigns it.
      const optimisticUserId = `local-${crypto.randomUUID()}`
      const optimisticAssistantId = `local-${crypto.randomUUID()}`
      const nowIso = new Date().toISOString()
      const seqBase = messages.length

      const optimisticUser: ChatMessage = {
        id: optimisticUserId,
        conversation_id: conversationId ?? "",
        sequence: seqBase + 1,
        role: "user",
        content,
        created_at: nowIso,
        source_selection: sel,
        context_manifest: null,
        input_tokens: null,
        output_tokens: null,
        llm_model: null,
        llm_finish_reason: null,
        llm_error: null,
      }
      const optimisticAssistant: ChatMessage = {
        id: optimisticAssistantId,
        conversation_id: conversationId ?? "",
        sequence: seqBase + 2,
        role: "assistant",
        content: "",
        created_at: nowIso,
        source_selection: null,
        context_manifest: null,
        input_tokens: null,
        output_tokens: null,
        llm_model: null,
        llm_finish_reason: null,
        llm_error: null,
      }
      setMessages((prev) => [...prev, optimisticUser, optimisticAssistant])
      setStreamingAssistantId(optimisticAssistantId)

      // Create conversation lazily if needed.
      let activeConversationId = conversationId
      if (!activeConversationId) {
        try {
          const created = await createConversation({
            patient_id: patientId,
            caller_feature_key: callerFeatureKey,
            caller_system_prompt: callerSystemPrompt,
            title: title || undefined,
            default_source_selection: defaultSourceSelection,
          })
          activeConversationId = created.id
          setConversationId(created.id)
          setConversationTitle(created.title)
          setServerDefault(created.default_source_selection ?? {})
        } catch (exc) {
          setMessages((prev) =>
            prev.filter(
              (m) => m.id !== optimisticUserId && m.id !== optimisticAssistantId,
            ),
          )
          setStreamingAssistantId(null)
          setError({
            code: "llm_error",
            serverMessage:
              exc instanceof Error ? exc.message : "Failed to create conversation.",
            retryable: true,
          })
          return
        }
      }

      await streamChatMessages(
        activeConversationId,
        { content, source_selection: sel },
        {
          onMeta: (meta) => {
            setLatestManifest(meta.manifest)
            setMessages((prev) =>
              prev.map((m) => {
                if (m.id === optimisticUserId) {
                  return {
                    ...m,
                    id: meta.user_message_id,
                    conversation_id: activeConversationId!,
                    context_manifest: meta.manifest,
                    input_tokens: meta.input_tokens,
                  }
                }
                if (m.id === optimisticAssistantId) {
                  return {
                    ...m,
                    id: meta.assistant_message_id,
                    conversation_id: activeConversationId!,
                    llm_model: meta.model,
                    // The manifest is logically owned by the user turn,
                    // but the disclosure renders under the assistant
                    // bubble per design doc §13.3. Mirror it here so
                    // ``MessageBubble`` can read it without parent
                    // bookkeeping.
                    context_manifest: meta.manifest,
                  }
                }
                return m
              }),
            )
            setStreamingAssistantId(meta.assistant_message_id)
          },
          onDelta: (delta) => {
            setStreamingAssistantId((currentId) => {
              if (!currentId) return currentId
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === currentId ? { ...m, content: m.content + delta.text } : m,
                ),
              )
              return currentId
            })
          },
          onDone: (done) => {
            setStreamingAssistantId((currentId) => {
              if (!currentId) return null
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === currentId
                    ? {
                        ...m,
                        output_tokens: done.output_tokens,
                        llm_finish_reason: done.finish_reason,
                      }
                    : m,
                ),
              )
              return null
            })
          },
          onError: (evt) => {
            const retryable =
              evt.error === "llm_error" ||
              evt.error === "timeout" ||
              evt.error === "service_unavailable"
            setMessages((prev) =>
              prev.filter(
                (m) =>
                  m.id !== optimisticAssistantId &&
                  m.id !== (streamingAssistantId ?? ""),
              ),
            )
            setStreamingAssistantId(null)
            setError({ code: evt.error, serverMessage: evt.message, retryable })
          },
        },
      )
    },
    [
      callerFeatureKey,
      callerSystemPrompt,
      conversationId,
      defaultSourceSelection,
      messages.length,
      patientId,
      streamingAssistantId,
      title,
    ],
  )

  const handleSend = useCallback(
    (content: string) => {
      void performSend(content, selection)
    },
    [performSend, selection],
  )

  const handleRetry = useCallback(() => {
    const cached = lastSendRef.current
    if (!cached) return
    void performSend(cached.content, cached.selection)
  }, [performSend])

  const handleResetToDefaults = useCallback(() => {
    setSelection(serverDefault)
    setError(null)
  }, [serverDefault])

  // -------------------------------------------------------------------------
  // Chip rail handlers
  // -------------------------------------------------------------------------

  const handleToggleSource = useCallback((key: SourceKey) => {
    setSelection((prev) => {
      const next = { ...prev }
      if (next[key]) {
        delete next[key]
      } else {
        next[key] = true
      }
      return next
    })
  }, [])

  const handleAddSource = useCallback((key: SourceKey) => {
    setSelection((prev) => ({ ...prev, [key]: true }))
  }, [])

  const handleSetAsDefault = useCallback(
    async (key: SourceKey) => {
      if (!conversationId) {
        // Pre-conversation: just update local default + selection.
        const nextDefault: SourceSelection = { ...serverDefault, [key]: selection[key] ?? true }
        setServerDefault(nextDefault)
        return
      }
      const nextDefault: SourceSelection = { ...serverDefault }
      if (selection[key]) {
        nextDefault[key] = selection[key]
      } else {
        delete nextDefault[key]
      }
      try {
        const updated = await updateConversation(conversationId, {
          default_source_selection: nextDefault,
        })
        setServerDefault(updated.default_source_selection ?? {})
      } catch (exc) {
        setError({
          code: "llm_error",
          serverMessage:
            exc instanceof Error ? exc.message : "Failed to update default.",
          retryable: false,
        })
      }
    },
    [conversationId, selection, serverDefault],
  )

  // -------------------------------------------------------------------------
  // Archive
  // -------------------------------------------------------------------------

  const handleArchive = useCallback(async () => {
    if (!conversationId) return
    await updateConversation(conversationId, { archive: true })
    setArchived(true)
    onArchived?.(conversationId)
  }, [conversationId, onArchived])

  // -------------------------------------------------------------------------
  // Detail dialog
  // -------------------------------------------------------------------------

  const isDetailDefault = useMemo(() => {
    if (!detailFor) return false
    return Boolean(serverDefault[detailFor])
  }, [detailFor, serverDefault])

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  if (hydrating) {
    return (
      <div
        data-slot="chat-panel"
        className={cn("flex h-full items-center justify-center", className)}
      >
        <p className="text-sm text-neutral-500">Loading conversation…</p>
      </div>
    )
  }

  return (
    <div
      data-slot="chat-panel"
      className={cn(
        "flex h-full max-w-[760px] flex-col gap-3 mx-auto",
        className,
      )}
    >
      {/* Header */}
      <div
        data-slot="chat-panel-header"
        className="flex items-center justify-between gap-2"
      >
        <h2 className="font-display text-lg font-medium text-neutral-900 truncate">
          {conversationTitle || title || "Chat"}
        </h2>
        {conversationId && !archived ? (
          <ArchiveButton onConfirm={handleArchive} />
        ) : null}
      </div>

      {/* Source chip rail */}
      <SourceChipRail
        selection={selection}
        latestManifest={latestManifest}
        onToggle={handleToggleSource}
        onOpenDetail={(key) => setDetailFor(key)}
        onAdd={handleAddSource}
      />

      {/* Message stream */}
      <div
        data-slot="chat-message-stream"
        className="flex-1 min-h-0 overflow-y-auto rounded-2xl border border-neutral-200 bg-neutral-50/40 p-4 space-y-3"
      >
        {messages.length === 0 && !streamingAssistantId ? (
          <p className="text-sm text-neutral-500 italic">
            Ask a question to start the conversation.
          </p>
        ) : null}
        {messages.map((message) => (
          <MessageBubble
            key={message.id}
            message={message}
            streaming={message.id === streamingAssistantId}
          />
        ))}
        {error ? (
          <ChatErrorNotice
            code={error.code}
            serverMessage={error.serverMessage}
            onResetToDefaults={
              error.code === "context_too_large" ||
              error.code === "invalid_selection"
                ? handleResetToDefaults
                : undefined
            }
            onRetry={error.retryable ? handleRetry : undefined}
          />
        ) : null}
        <div ref={messagesEndRef} />
      </div>

      {/* Composer */}
      {archived ? (
        <p
          data-slot="chat-archived-footer"
          className="text-center text-xs text-neutral-500 py-2"
        >
          This conversation is archived.
        </p>
      ) : (
        <Composer
          contextTokens={contextTokens}
          tokenBudget={tokenBudget}
          disabled={composerDisabled}
          onSend={handleSend}
        />
      )}

      {/* Source-chip detail dialog (single instance shared by all chips) */}
      <SourceChipDetail
        open={detailFor !== null}
        sourceKey={detailFor}
        manifest={latestManifest}
        isDefault={isDetailDefault}
        onOpenChange={(open) => !open && setDetailFor(null)}
        onSetAsDefault={(key) => {
          void handleSetAsDefault(key)
          setDetailFor(null)
        }}
      />
    </div>
  )
}
