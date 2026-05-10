// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ChatPanel — patient-context chat surface (OSS primitive).
 *
 * Prop-driven and prompt-neutral: the caller supplies the system prompt,
 * the source-selection default, and the `callerFeatureKey` used for
 * audit + analytics tagging. The component handles conversation
 * lifecycle (lazy create on first send), SSE streaming, error states,
 * and the "what the model saw" disclosure.
 *
 * Empty-state copy is deliberately generic — surfaces that wrap this
 * component supply their own framing.
 */

"use client"

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  createChatConversation,
  fetchChatConversation,
  sendChatMessage,
  updateChatConversation,
} from "@/lib/api/chat"
import type {
  ChatContextManifest,
  ChatConversation,
  ChatMessage,
  ChatSourceSelection,
  ChatStreamEvent,
} from "@/types/chat"

export interface ChatPanelProps {
  patientId: string
  callerFeatureKey: string
  callerSystemPrompt: string
  defaultSourceSelection: ChatSourceSelection
  initialTitle?: string
  conversationId?: string
  onConversationCreated?: (id: string) => void
  onMessageStreamed?: (message: ChatMessage) => void
  className?: string
}

interface RenderedMessage {
  id: string
  role: "user" | "assistant"
  content: string
  manifest?: ChatContextManifest | null
  pending?: boolean
  errorKind?: string
  errorMessage?: string
}

export function ChatPanel(props: ChatPanelProps) {
  const {
    patientId,
    callerFeatureKey,
    callerSystemPrompt,
    defaultSourceSelection,
    initialTitle,
    conversationId: initialConversationId,
    onConversationCreated,
    onMessageStreamed,
    className,
  } = props

  const [conversation, setConversation] = useState<ChatConversation | null>(null)
  const [messages, setMessages] = useState<RenderedMessage[]>([])
  const [draft, setDraft] = useState("")
  const [streaming, setStreaming] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // Hydrate an existing conversation if a conversationId was provided.
  useEffect(() => {
    if (!initialConversationId) {
      setConversation(null)
      setMessages([])
      return
    }
    let cancelled = false
    fetchChatConversation(initialConversationId)
      .then((detail) => {
        if (cancelled) return
        setConversation(detail)
        setMessages(
          detail.messages.map((m) => ({
            id: m.id,
            role: m.role,
            content: m.content,
            manifest: m.context_manifest,
          })),
        )
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setLoadError(err instanceof Error ? err.message : "Failed to load chat")
      })
    return () => {
      cancelled = true
    }
  }, [initialConversationId])

  const ensureConversation = useCallback(async (): Promise<ChatConversation> => {
    if (conversation) return conversation
    const created = await createChatConversation({
      patient_id: patientId,
      caller_feature_key: callerFeatureKey,
      caller_system_prompt: callerSystemPrompt,
      title: initialTitle,
      default_source_selection: defaultSourceSelection,
    })
    setConversation(created)
    onConversationCreated?.(created.id)
    return created
  }, [
    callerFeatureKey,
    callerSystemPrompt,
    conversation,
    defaultSourceSelection,
    initialTitle,
    onConversationCreated,
    patientId,
  ])

  const handleSend = useCallback(async () => {
    const trimmed = draft.trim()
    if (!trimmed || streaming) return

    setStreaming(true)
    setDraft("")
    let userMessageId: string | null = null
    let assistantMessageId: string | null = null
    let assistantContent = ""

    try {
      const conv = await ensureConversation()
      // Optimistic user-turn render — we'll replace ids when meta arrives.
      const optimisticUserId = `pending-user-${Date.now()}`
      const optimisticAssistantId = `pending-assistant-${Date.now()}`
      setMessages((prev) => [
        ...prev,
        { id: optimisticUserId, role: "user", content: trimmed },
        { id: optimisticAssistantId, role: "assistant", content: "", pending: true },
      ])

      abortRef.current?.abort()
      const ctrl = new AbortController()
      abortRef.current = ctrl

      const stream = sendChatMessage(
        conv.id,
        { content: trimmed },
        { signal: ctrl.signal },
      )

      for await (const event of stream as AsyncGenerator<ChatStreamEvent>) {
        if (event.kind === "meta") {
          userMessageId = event.user_message_id
          assistantMessageId = event.assistant_message_id
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id === optimisticUserId) {
                return { ...m, id: event.user_message_id }
              }
              if (m.id === optimisticAssistantId) {
                return { ...m, id: event.assistant_message_id }
              }
              return m
            }),
          )
        } else if (event.kind === "delta") {
          assistantContent += event.text
          const targetId = assistantMessageId ?? optimisticAssistantId
          setMessages((prev) =>
            prev.map((m) =>
              m.id === targetId
                ? { ...m, content: assistantContent, pending: true }
                : m,
            ),
          )
        } else if (event.kind === "done") {
          const targetId = assistantMessageId ?? optimisticAssistantId
          setMessages((prev) =>
            prev.map((m) =>
              m.id === targetId
                ? { ...m, content: assistantContent, pending: false }
                : m,
            ),
          )
          onMessageStreamed?.({
            id: targetId,
            conversation_id: conv.id,
            sequence: 0,
            role: "assistant",
            content: assistantContent,
            created_at: new Date().toISOString(),
            context_manifest: null,
            input_tokens: null,
            output_tokens: event.output_tokens,
            llm_model: null,
            llm_finish_reason: event.finish_reason,
            llm_error: null,
          })
        } else if (event.kind === "error") {
          const targetId = assistantMessageId ?? optimisticAssistantId
          setMessages((prev) =>
            prev.map((m) =>
              m.id === targetId
                ? {
                    ...m,
                    content: assistantContent,
                    pending: false,
                    errorKind: event.error,
                    errorMessage: event.message,
                  }
                : m,
            ),
          )
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error"
      setMessages((prev) =>
        prev.map((m) =>
          m.pending
            ? { ...m, pending: false, errorKind: "client_error", errorMessage: message }
            : m,
        ),
      )
    } finally {
      setStreaming(false)
      abortRef.current = null
      // Suppress unused-var lint for tracked ids — they're used for
      // optimistic-update bookkeeping above.
      void userMessageId
      void assistantMessageId
    }
  }, [draft, ensureConversation, onMessageStreamed, streaming])

  const handleArchive = useCallback(async () => {
    if (!conversation) return
    const updated = await updateChatConversation(conversation.id, {
      archive: true,
    })
    setConversation(updated)
  }, [conversation])

  const lastManifest: ChatContextManifest | null = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const manifest = messages[i].manifest
      if (manifest) return manifest
    }
    return null
  }, [messages])

  return (
    <div className={className ?? "flex h-full flex-col gap-3"}>
      {loadError ? (
        <div role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          {loadError}
        </div>
      ) : null}

      <div className="flex-1 space-y-3 overflow-y-auto" data-testid="chat-messages">
        {messages.length === 0 ? (
          <p className="text-sm text-muted-foreground">Start a conversation.</p>
        ) : null}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
      </div>

      {lastManifest ? <ContextDisclosure manifest={lastManifest} /> : null}

      <div className="flex flex-col gap-2">
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Type a message…"
          rows={3}
          disabled={streaming || conversation?.archived_at != null}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault()
              void handleSend()
            }
          }}
        />
        <div className="flex items-center justify-between">
          <div className="text-xs text-muted-foreground">
            {streaming ? "Streaming…" : "Cmd/Ctrl + Enter to send"}
          </div>
          <div className="flex gap-2">
            {conversation && conversation.archived_at == null ? (
              <Button variant="ghost" type="button" onClick={handleArchive}>
                Archive
              </Button>
            ) : null}
            <Button
              type="button"
              onClick={handleSend}
              disabled={streaming || draft.trim().length === 0 || conversation?.archived_at != null}
            >
              Send
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

function MessageBubble({ message }: { message: RenderedMessage }) {
  const isUser = message.role === "user"
  return (
    <div
      data-testid={`chat-message-${message.role}`}
      className={
        "rounded-lg p-3 text-sm " +
        (isUser
          ? "bg-secondary text-secondary-foreground"
          : "bg-muted text-foreground")
      }
    >
      <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">
        {isUser ? "You" : "Assistant"}
      </div>
      {message.content ? (
        <div className="whitespace-pre-wrap">{message.content}</div>
      ) : message.pending ? (
        <div className="text-muted-foreground">…</div>
      ) : null}
      {message.errorMessage ? (
        <div className="mt-2 text-xs text-red-700" role="alert">
          {errorLabelFor(message.errorKind)}: {message.errorMessage}
        </div>
      ) : null}
    </div>
  )
}

function errorLabelFor(kind?: string): string {
  switch (kind) {
    case "safety_block":
      return "Output blocked by safety filter"
    case "context_too_large":
      return "Context too large"
    case "quota_exceeded":
      return "Monthly usage limit reached"
    case "llm_error":
      return "Model call failed"
    default:
      return "Error"
  }
}

function ContextDisclosure({ manifest }: { manifest: ChatContextManifest }) {
  return (
    <details className="rounded border border-border bg-card text-xs">
      <summary className="cursor-pointer p-2 font-medium">
        What the model saw ({manifest.total_tokens_est.toLocaleString()} tokens)
      </summary>
      <div className="space-y-2 p-2">
        <ul className="list-disc pl-4">
          {manifest.sources_included.map((s) => (
            <li key={s.source_key}>
              <code>{s.source_key}</code>
              {typeof s.tokens_est === "number"
                ? ` — ${s.tokens_est.toLocaleString()} tokens`
                : null}
              {s.status ? ` (${s.status})` : null}
            </li>
          ))}
        </ul>
        {manifest.sources_dropped.length > 0 ? (
          <div>
            <div className="font-medium">Dropped to fit:</div>
            <ul className="list-disc pl-4">
              {manifest.sources_dropped.map((d) => (
                <li key={d.source_key}>
                  <code>{d.source_key}</code> — {d.reason}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </details>
  )
}
