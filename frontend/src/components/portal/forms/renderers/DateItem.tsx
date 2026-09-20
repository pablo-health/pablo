// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A date, in whatever window the question allows.
 *
 * The answer is `{value}` as `YYYY-MM-DD`, which is what a native date input
 * produces and what the save route parses. Native rather than a calendar of
 * our own: the platform's picker knows the person's locale, their keyboard
 * and their screen reader, and a date of birth is faster typed than clicked.
 *
 * `past_only` narrows the top of the window to today. Computed here only to
 * fill the input's `max`; whether a date is acceptable is the server's
 * answer, and it is judged against one consistent day rather than against a
 * clock that moved while the form was open.
 */

import { Input } from "@/components/ui/input"
import { QuestionFrame, labelOf } from "./QuestionFrame"
import type { AnswerValue, ItemRenderer, ItemRendererProps } from "./types"

function isoDate(config: Record<string, unknown>, key: "min" | "max"): string | undefined {
  return typeof config[key] === "string" ? (config[key] as string) : undefined
}

/** Today as `YYYY-MM-DD`, for a question that only accepts a past date. */
function today(): string {
  return new Date().toISOString().slice(0, 10)
}

function latestAllowed(config: Record<string, unknown>): string | undefined {
  const max = isoDate(config, "max")
  if (config.past_only !== true) return max
  const now = today()
  return max !== undefined && max < now ? max : now
}

function valueIn(value: AnswerValue | null): string {
  return typeof value?.value === "string" ? value.value : ""
}

function DateItem({ item, value, onChange }: ItemRendererProps) {
  return (
    <QuestionFrame item={item}>
      {(headingId) => (
        <Input
          type="date"
          data-testid="forms-date"
          aria-labelledby={headingId}
          className="max-w-[12rem]"
          min={isoDate(item.config, "min")}
          max={latestAllowed(item.config)}
          value={valueIn(value)}
          onChange={(e) => onChange(e.target.value === "" ? {} : { value: e.target.value })}
        />
      )}
    </QuestionFrame>
  )
}

export const dateRenderer: ItemRenderer = {
  Component: DateItem,
  answerable: true,
  label: labelOf,
  summary: (value) => {
    const current = valueIn(value)
    return current === "" ? null : current
  },
}
