// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What brings you in.
 *
 * The prompt is the server's (`reason_prompt`), not this file's, for the
 * same reason the measures' item text is: the engine owns the wording of
 * the questions it asks itself.
 *
 * The answer is `{text}` because that is what the save route stores. The cap
 * mirrors the route's, so a patient meets the limit while typing rather than
 * after pressing Continue.
 */

import { Textarea } from "@/components/ui/textarea"
import type { AnswerValue, ItemRenderer, ItemRendererProps } from "./types"

/** Matches `REASON_MAX_LEN` in `backend/app/intake/answers.py`. */
export const REASON_MAX = 4_000

/** The heading when the server's prompt has not arrived. */
const FALLBACK_PROMPT = "What brings you in?"

function textIn(value: AnswerValue | null): string {
  const text = value?.text
  return typeof text === "string" ? text : ""
}

function ReasonItem({ value, onChange, form }: ItemRendererProps) {
  const text = textIn(value)
  return (
    <section aria-labelledby="forms-reason-heading">
      <h2 id="forms-reason-heading" className="text-lg font-semibold text-neutral-900">
        {form?.reason_prompt ?? FALLBACK_PROMPT}
      </h2>
      <div className="mt-4 space-y-1.5">
        <Textarea
          id="forms-reason"
          data-testid="forms-reason"
          aria-labelledby="forms-reason-heading"
          className="min-h-[9rem]"
          value={text}
          maxLength={REASON_MAX}
          onChange={(e) => onChange({ text: e.target.value })}
        />
        <p data-testid="forms-reason-counter" className="text-right text-xs text-neutral-500">
          {text.length} / {REASON_MAX}
        </p>
      </div>
    </section>
  )
}

export const reasonRenderer: ItemRenderer = {
  Component: ReasonItem,
  answerable: true,
  label: (_item, form) => form?.reason_prompt ?? FALLBACK_PROMPT,
  summary: (value) => {
    const text = textIn(value).trim()
    return text === "" ? null : text
  },
}
