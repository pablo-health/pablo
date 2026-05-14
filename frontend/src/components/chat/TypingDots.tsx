// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Three-dot streaming indicator. Rendered at the tail of an assistant
 * bubble while ``delta`` events are flowing. Animation uses Tailwind's
 * built-in ``animate-bounce`` with staggered delays so we don't have
 * to register a custom keyframe in globals.css.
 */

import { cn } from "@/lib/utils"

interface TypingDotsProps {
  className?: string
}

export function TypingDots({ className }: TypingDotsProps) {
  return (
    <span
      data-slot="chat-typing-dots"
      className={cn("inline-flex items-end gap-1", className)}
      aria-label="Assistant is composing"
      role="status"
    >
      <span className="size-1.5 rounded-full bg-primary-500/70 animate-bounce [animation-delay:-0.3s]" />
      <span className="size-1.5 rounded-full bg-primary-500/70 animate-bounce [animation-delay:-0.15s]" />
      <span className="size-1.5 rounded-full bg-primary-500/70 animate-bounce" />
    </span>
  )
}
