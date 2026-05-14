// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Inline error tile rendered as the last entry in the message stream
 * when a turn fails (§13.8). Copy is *non-clinical and action-oriented*;
 * the remedy button (if any) wires into the panel's recovery path.
 */

import { AlertCircle } from "lucide-react"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import type { ChatErrorCode } from "@/lib/chat/types"

interface ChatErrorNoticeProps {
  code: ChatErrorCode | string
  /** Server-supplied message, used as fallback if we have no specific copy. */
  serverMessage?: string
  /** Restore the conversation's ``default_source_selection``. */
  onResetToDefaults?: () => void
  /** Re-send the failed turn. */
  onRetry?: () => void
}

interface ErrorCopy {
  body: string
  remedy?: "reset" | "retry"
}

const COPY: Record<string, ErrorCopy> = {
  context_too_large: {
    body: "The selected sources are too large for a single reply. Try unchecking older notes or removing pasted text.",
    remedy: "reset",
  },
  safety_block: {
    body: "The model declined to respond. Try rephrasing or shortening the question.",
  },
  llm_error: {
    body: "We couldn't reach the model.",
    remedy: "retry",
  },
  timeout: {
    body: "We couldn't reach the model.",
    remedy: "retry",
  },
  service_unavailable: {
    body: "We couldn't reach the model.",
    remedy: "retry",
  },
  concurrent_turn: {
    body: "Another response is still streaming for this conversation.",
  },
  quota_exceeded: {
    body: "Chat quota for this period has been reached.",
  },
  invalid_selection: {
    body: "One of the selected sources isn't available.",
    remedy: "reset",
  },
  auth_denied: {
    body: "You're not signed in to use chat.",
  },
}

export function ChatErrorNotice({
  code,
  serverMessage,
  onResetToDefaults,
  onRetry,
}: ChatErrorNoticeProps) {
  const copy = COPY[code]
  const body = copy?.body ?? serverMessage ?? "Something went wrong."
  const remedy = copy?.remedy

  return (
    <div
      data-slot="chat-error-notice"
      data-error-code={code}
      role="alert"
      className={cn(
        "flex items-start gap-2 rounded-xl border border-danger-100 bg-danger-100/30 px-3 py-2",
        "text-sm text-danger-800",
      )}
    >
      <AlertCircle className="size-4 mt-0.5 shrink-0" aria-hidden="true" />
      <div className="flex-1 min-w-0">
        <p>{body}</p>
        {remedy === "reset" && onResetToDefaults ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="mt-2 border-danger-800/20 text-danger-800 hover:bg-danger-100/60"
            onClick={onResetToDefaults}
          >
            Reset to defaults
          </Button>
        ) : null}
        {remedy === "retry" && onRetry ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="mt-2 border-danger-800/20 text-danger-800 hover:bg-danger-100/60"
            onClick={onRetry}
          >
            Retry
          </Button>
        ) : null}
      </div>
    </div>
  )
}
