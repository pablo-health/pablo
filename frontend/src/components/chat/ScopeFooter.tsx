// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Scope / safety footer (§13.7). Persistent single line below the
 * composer. Static copy, no link. Matches the APA chatbot health
 * advisory's "clear, prominent disclaimer" requirement — factual
 * scope statement, not clinical voice.
 */

import { cn } from "@/lib/utils"

export interface ScopeFooterProps {
  className?: string
}

export const SCOPE_FOOTER_TEXT =
  "Pablo Chat summarizes chart context. Not a clinical decision tool. " +
  "PHI stays in this practice; conversations are purged on delete."

export function ScopeFooter({ className }: ScopeFooterProps) {
  return (
    <p
      data-slot="chat-scope-footer"
      className={cn(
        "text-center text-xs text-neutral-500 px-2",
        className,
      )}
    >
      {SCOPE_FOOTER_TEXT}
    </p>
  )
}
