// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A box to write in, with the practice's own question above it.
 *
 * The answer is `{text}` and the cap is the item's `max_len`, so a patient
 * meets the limit while typing rather than after pressing Continue. The
 * default matches `FreeTextConfig`'s, which is what a form saved before the
 * practice touched that setting stores.
 */

import { Textarea } from "@/components/ui/textarea"
import { QuestionFrame, labelOf } from "./QuestionFrame"
import type { AnswerValue, ItemRenderer, ItemRendererProps } from "./types"

/** Matches `FreeTextConfig.max_len`'s default in `backend/app/intake/items.py`. */
export const FREE_TEXT_DEFAULT_MAX = 2_000

function maxLenOf(config: Record<string, unknown>): number {
  return typeof config.max_len === "number" ? config.max_len : FREE_TEXT_DEFAULT_MAX
}

function textIn(value: AnswerValue | null): string {
  return typeof value?.text === "string" ? value.text : ""
}

function FreeTextItem({ item, value, onChange }: ItemRendererProps) {
  const text = textIn(value)
  const max = maxLenOf(item.config)
  return (
    <QuestionFrame item={item}>
      {(headingId) => (
        <div className="space-y-1.5">
          <Textarea
            data-testid="forms-free-text"
            aria-labelledby={headingId}
            className="min-h-[7rem]"
            value={text}
            maxLength={max}
            onChange={(e) => onChange({ text: e.target.value })}
          />
          <p data-testid="forms-free-text-counter" className="text-right text-xs text-neutral-500">
            {text.length} / {max}
          </p>
        </div>
      )}
    </QuestionFrame>
  )
}

export const freeTextRenderer: ItemRenderer = {
  Component: FreeTextItem,
  answerable: true,
  label: labelOf,
  summary: (value) => {
    const text = textIn(value).trim()
    return text === "" ? null : text
  },
}
