// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Composer (§13.10). Auto-resize textarea + honey send button + a thin
 * preflight token-budget meter under the textarea (sage → amber → red
 * across 50% / 75% / 95% of remaining budget).
 *
 * The meter is a *preflight* signal, not a hard gate — the backend is
 * the source of truth on whether the assembled context fits. The
 * meter's job is to surface the ``context_too_large`` failure mode
 * before the user hits send.
 */

import { useEffect, useRef, useState, type KeyboardEvent } from "react"
import { Send } from "lucide-react"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

interface ComposerProps {
  /** Tokens already consumed by the assembled context (not the user message). */
  contextTokens: number
  tokenBudget: number
  disabled?: boolean
  placeholder?: string
  onSend: (content: string) => void
}

const CHARS_PER_TOKEN = 4
const MAX_CONTENT_CHARS = 32_000

export function Composer({
  contextTokens,
  tokenBudget,
  disabled = false,
  placeholder = "Ask a question…",
  onSend,
}: ComposerProps) {
  const [value, setValue] = useState("")
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = "auto"
    const next = Math.min(el.scrollHeight, 200) // ~8 lines
    el.style.height = `${next}px`
  }, [value])

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  function submit() {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setValue("")
  }

  const projectedTokens = contextTokens + Math.ceil(value.length / CHARS_PER_TOKEN)
  const remaining = Math.max(tokenBudget - contextTokens, 1)
  const fraction = Math.min((projectedTokens - contextTokens) / remaining, 1)
  const meterColor = fraction >= 0.95 ? "bg-danger-800" : fraction >= 0.75 ? "bg-primary-500" : "bg-secondary-500"
  const showMeter = fraction >= 0.5
  const overChars = value.length > MAX_CONTENT_CHARS

  return (
    <div data-slot="chat-composer" className="flex flex-col gap-1">
      <div className="flex items-end gap-2 rounded-2xl border border-neutral-200 bg-card px-3 py-2 shadow-sm focus-within:border-neutral-400 transition-colors">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          maxLength={MAX_CONTENT_CHARS + 1000 /* allow visible overflow */}
          rows={1}
          aria-label="Message"
          className={cn(
            "flex-1 resize-none bg-transparent text-sm leading-relaxed text-neutral-900",
            "placeholder:text-neutral-400 focus:outline-none disabled:opacity-60",
            "min-h-[24px]",
          )}
        />
        <Button
          type="button"
          size="icon"
          onClick={submit}
          disabled={disabled || value.trim().length === 0 || overChars}
          aria-label="Send"
          className="shrink-0 self-end mb-0.5 bg-primary-500 hover:bg-primary-600 text-white"
        >
          <Send className="size-4" />
        </Button>
      </div>
      <div className="flex items-center justify-between min-h-[14px]">
        {showMeter ? (
          <div
            role="progressbar"
            aria-label="Context budget used"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(fraction * 100)}
            className="flex-1 h-0.5 rounded-full bg-neutral-100 overflow-hidden mr-3"
          >
            <div
              className={cn("h-full transition-all duration-200", meterColor)}
              style={{ width: `${Math.round(fraction * 100)}%` }}
            />
          </div>
        ) : (
          <span />
        )}
        <span className="text-[10px] text-neutral-400">
          {overChars ? "Message too long" : ""}
        </span>
      </div>
    </div>
  )
}
