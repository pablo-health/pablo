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
import { ChevronRight } from "lucide-react"
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

  const scrollRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  return (
    <div className={className ?? "flex h-full flex-col gap-4"}>
      {loadError ? (
        <div
          role="alert"
          className="rounded-lg border border-danger-100 bg-danger-100/30 px-3 py-2 text-sm text-danger-800"
        >
          {loadError}
        </div>
      ) : null}

      <div
        ref={scrollRef}
        className="flex-1 space-y-4 overflow-y-auto py-2"
        data-testid="chat-messages"
      >
        {messages.length === 0 ? (
          <p className="text-sm text-neutral-500">Start a conversation.</p>
        ) : null}
        {messages.map((m, i) => (
          <MessageBubble
            key={m.id}
            message={m}
            // Only the most recent assistant turn is announced live, so
            // earlier replies don't re-announce on every re-render.
            isLatest={i === messages.length - 1}
          />
        ))}
      </div>

      {lastManifest ? <ContextDisclosure manifest={lastManifest} /> : null}

      <div className="flex flex-col gap-2 rounded-xl border border-neutral-200 bg-white p-3 shadow-sm">
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Type a message…"
          rows={3}
          disabled={streaming || conversation?.archived_at != null}
          aria-keyshortcuts="Enter"
          className="resize-none border-neutral-200 bg-white text-neutral-900 placeholder:text-neutral-400"
          onKeyDown={(e) => {
            // Pablo-native pattern: plain Enter sends, Shift+Enter
            // inserts a newline. Cmd/Ctrl+Enter retained as an alias
            // for users who learned it elsewhere.
            const isSend =
              e.key === "Enter" &&
              !e.shiftKey &&
              !e.nativeEvent.isComposing
            if (isSend) {
              e.preventDefault()
              void handleSend()
            }
          }}
        />
        <div className="flex items-center justify-between">
          <div className="text-xs text-neutral-500">
            {streaming ? (
              <span className="inline-flex items-center gap-2">
                <StreamingDots />
                <span>Streaming…</span>
              </span>
            ) : (
              <span>Enter to send · Shift+Enter for newline</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {conversation && conversation.archived_at == null ? (
              <Button
                variant="ghost"
                size="sm"
                type="button"
                onClick={handleArchive}
              >
                Archive
              </Button>
            ) : null}
            <Button
              type="button"
              onClick={handleSend}
              disabled={
                streaming ||
                draft.trim().length === 0 ||
                conversation?.archived_at != null
              }
            >
              Send
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

function MessageBubble({
  message,
  isLatest,
}: {
  message: RenderedMessage
  isLatest: boolean
}) {
  const isUser = message.role === "user"
  const ariaLive = !isUser && isLatest && message.pending ? "polite" : undefined
  return (
    <div
      data-testid={`chat-message-${message.role}`}
      className={"flex w-full " + (isUser ? "justify-end" : "justify-start")}
    >
      <div
        aria-live={ariaLive}
        aria-atomic="false"
        className={
          "max-w-[85%] px-4 py-3 text-sm leading-relaxed text-neutral-900 " +
          (isUser
            ? "rounded-2xl rounded-br-sm bg-primary-100"
            : "rounded-2xl rounded-bl-sm border border-neutral-200 bg-white shadow-sm")
        }
      >
        {message.content ? (
          <div className="whitespace-pre-wrap">{message.content}</div>
        ) : message.pending ? (
          <PendingDots />
        ) : null}
        {message.errorMessage && !message.pending ? (
          <div
            className="mt-2 rounded-md bg-danger-100/60 px-2 py-1 text-xs text-danger-800"
            role="alert"
          >
            {errorLabelFor(message.errorKind)}: {message.errorMessage}
          </div>
        ) : null}
      </div>
    </div>
  )
}

function StreamingDots() {
  return (
    <span aria-hidden="true" className="inline-flex items-end gap-0.5">
      <span className="size-1.5 animate-bounce rounded-full bg-primary-400 [animation-delay:-0.3s]" />
      <span className="size-1.5 animate-bounce rounded-full bg-primary-400 [animation-delay:-0.15s]" />
      <span className="size-1.5 animate-bounce rounded-full bg-primary-400" />
    </span>
  )
}

function PendingDots() {
  return (
    <span className="inline-flex items-end gap-1 py-1" aria-label="Assistant is typing">
      <span className="size-2 animate-bounce rounded-full bg-neutral-300 [animation-delay:-0.3s]" />
      <span className="size-2 animate-bounce rounded-full bg-neutral-300 [animation-delay:-0.15s]" />
      <span className="size-2 animate-bounce rounded-full bg-neutral-300" />
    </span>
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
    <details className="group border-t border-neutral-200 pt-3 text-xs text-neutral-700">
      <summary className="flex cursor-pointer select-none items-center gap-1.5 text-neutral-600 transition-colors hover:text-primary-700">
        <ChevronRight className="size-3 transition-transform group-open:rotate-90" />
        <span>
          What the model saw{" "}
          <span className="text-neutral-400">
            ({manifest.total_tokens_est.toLocaleString()} tokens)
          </span>
        </span>
      </summary>
      <div className="space-y-3 pl-4 pt-2">
        <ul className="space-y-1">
          {manifest.sources_included.map((s) => (
            <li key={s.source_key} className="flex items-baseline gap-2">
              <code className="rounded bg-neutral-100 px-1.5 py-0.5 text-[11px] text-neutral-700">
                {s.source_key}
              </code>
              {typeof s.tokens_est === "number" ? (
                <span className="text-neutral-500">
                  {s.tokens_est.toLocaleString()} tokens
                </span>
              ) : null}
              {s.status ? (
                <span className="text-neutral-400">({s.status})</span>
              ) : null}
            </li>
          ))}
        </ul>
        {manifest.sources_dropped.length > 0 ? (
          <div>
            <div className="mb-1 text-neutral-600">Dropped to fit:</div>
            <ul className="space-y-1">
              {manifest.sources_dropped.map((d) => (
                <li key={d.source_key} className="flex items-baseline gap-2">
                  <code className="rounded bg-neutral-100 px-1.5 py-0.5 text-[11px] text-neutral-700">
                    {d.source_key}
                  </code>
                  <span className="text-neutral-500">— {d.reason}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </details>
  )
}
