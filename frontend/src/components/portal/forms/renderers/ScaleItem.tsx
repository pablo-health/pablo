// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A numbered scale with a word at each end.
 *
 * Every point is its own radio rather than a slider: a slider on a phone is
 * hard to land on a number and impossible to read back, and the points here
 * are a practice's own question rather than a validated instrument's, so
 * there is no published layout to reproduce.
 *
 * The answer is `{value}` as a whole number within the item's bounds. The
 * anchors are labels, never a reading of what a number means — a scale a
 * practice wrote has no bands and nothing here invents any.
 */

import { Unavailable } from "./DisplayItem"
import { QuestionFrame, labelOf } from "./QuestionFrame"
import type { AnswerValue, ItemRenderer, ItemRendererProps } from "./types"

/** The scale's ends, or null when the item was stored without them. */
function boundsOf(config: Record<string, unknown>): { min: number; max: number } | null {
  const { min, max } = config
  if (typeof min !== "number" || typeof max !== "number" || max <= min) return null
  return { min, max }
}

function anchorOf(config: Record<string, unknown>, key: "min_label" | "max_label"): string | null {
  const value = config[key]
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null
}

function pointsOf(bounds: { min: number; max: number }): number[] {
  return Array.from({ length: bounds.max - bounds.min + 1 }, (_, i) => bounds.min + i)
}

function valueIn(value: AnswerValue | null): number | null {
  return typeof value?.value === "number" ? value.value : null
}

function ScaleItem({ item, value, onChange }: ItemRendererProps) {
  const bounds = boundsOf(item.config)
  const chosen = valueIn(value)

  // Publishing refuses a scale without both ends, so a form a patient was
  // sent always carries them. Reading it back anyway keeps this the same
  // shape as every other renderer: nothing on this surface asks a question
  // it cannot draw.
  if (bounds === null) return <Unavailable />

  return (
    <QuestionFrame item={item}>
      {() => (
          <div className="space-y-2">
            <div
              role="radiogroup"
              data-testid="forms-scale"
              className="flex flex-wrap gap-1.5"
            >
              {pointsOf(bounds).map((point) => {
                const inputId = `forms-scale-${item.id}-${point}`
                const active = chosen === point
                return (
                  <label
                    key={point}
                    htmlFor={inputId}
                    className={
                      active
                        ? "flex h-11 w-11 items-center justify-center rounded-md border border-primary-400 bg-primary-50 text-sm font-medium text-primary-800"
                        : "flex h-11 w-11 items-center justify-center rounded-md border border-neutral-200 text-sm text-neutral-700"
                    }
                  >
                    <input
                      type="radio"
                      id={inputId}
                      name={`forms-scale-${item.id}`}
                      checked={active}
                      onChange={() => onChange({ value: point })}
                      className="sr-only"
                    />
                    {point}
                  </label>
                )
              })}
            </div>
            <div className="flex justify-between text-xs text-neutral-500">
              <span data-testid="forms-scale-min-label">{anchorOf(item.config, "min_label")}</span>
              <span data-testid="forms-scale-max-label">{anchorOf(item.config, "max_label")}</span>
            </div>
          </div>
      )}
    </QuestionFrame>
  )
}

export const scaleRenderer: ItemRenderer = {
  Component: ScaleItem,
  answerable: true,
  label: labelOf,
  summary: (value, item) => {
    const chosen = valueIn(value)
    if (chosen === null) return null
    const bounds = boundsOf(item.config)
    return bounds === null ? String(chosen) : `${chosen} of ${bounds.max}`
  },
}
