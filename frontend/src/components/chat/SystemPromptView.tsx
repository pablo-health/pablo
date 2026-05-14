// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * System-prompt view (§13.6) — a chevron + "i" affordance next to the
 * conversation title that expands a read-only disclosure of the
 * verbatim ``caller_system_prompt``. Closed by default. No edit
 * affordance — the prompt is immutable per §3.1.
 *
 * Answers the Frontiers 2025 "how on earth does AI decide?" question
 * literally: this is exactly what the model was told to do.
 */

import { useId, useState } from "react"
import { ChevronDown, ChevronRight, Info } from "lucide-react"

import { cn } from "@/lib/utils"

export interface SystemPromptViewProps {
  callerFeatureKey: string
  systemPrompt: string
  className?: string
}

export function SystemPromptView({
  callerFeatureKey,
  systemPrompt,
  className,
}: SystemPromptViewProps) {
  const [open, setOpen] = useState(false)
  const regionId = useId()

  return (
    <div data-slot="chat-system-prompt-view" className={className}>
      <button
        type="button"
        onClick={() => setOpen((p) => !p)}
        aria-expanded={open}
        aria-controls={regionId}
        aria-label={
          open ? "Hide system prompt" : "Show system prompt"
        }
        data-slot="chat-system-prompt-toggle"
        className={cn(
          "inline-flex items-center gap-0.5 rounded-md px-1 py-0.5",
          "text-neutral-500 hover:text-neutral-800 hover:bg-neutral-100",
          "transition-colors cursor-pointer",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-300",
        )}
      >
        {open ? (
          <ChevronDown className="size-3.5" aria-hidden="true" />
        ) : (
          <ChevronRight className="size-3.5" aria-hidden="true" />
        )}
        <Info className="size-3.5" aria-hidden="true" />
      </button>

      {open ? (
        <div
          id={regionId}
          role="region"
          data-slot="chat-system-prompt-region"
          className="mt-2 rounded-lg border border-neutral-200 bg-neutral-50/80 p-3"
        >
          <p className="text-xs text-neutral-600 mb-2">
            Using the{" "}
            <span className="font-medium text-neutral-900">
              {callerFeatureKey}
            </span>{" "}
            prompt:
          </p>
          <pre
            data-slot="chat-system-prompt-body"
            className="whitespace-pre-wrap font-mono text-[11px] leading-snug text-neutral-700 max-h-64 overflow-y-auto"
          >
            {systemPrompt}
          </pre>
        </div>
      ) : null}
    </div>
  )
}
