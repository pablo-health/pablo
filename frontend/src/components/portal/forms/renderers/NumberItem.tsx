// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A number, with the unit the practice asked for beside it.
 *
 * The answer is `{value}` as a number, and an empty box is no answer rather
 * than zero — `{value: 0}` and "they have not filled this in" are different
 * things, and only one of them should get a patient past the question.
 *
 * `min` and `max` ride on the input as bounds the browser can show, but the
 * refusal is the server's: a number typed outside them comes back named,
 * because a control that silently rejects a keystroke is the worst way to
 * tell somebody their answer is out of range.
 */

import { Input } from "@/components/ui/input"
import { QuestionFrame, labelOf } from "./QuestionFrame"
import type { AnswerValue, ItemRenderer, ItemRendererProps } from "./types"

function boundOf(config: Record<string, unknown>, key: "min" | "max"): number | undefined {
  return typeof config[key] === "number" ? (config[key] as number) : undefined
}

function unitOf(config: Record<string, unknown>): string | null {
  const unit = typeof config.unit === "string" ? config.unit.trim() : ""
  return unit === "" ? null : unit
}

function valueIn(value: AnswerValue | null): number | null {
  return typeof value?.value === "number" ? value.value : null
}

function NumberItem({ item, value, onChange }: ItemRendererProps) {
  const current = valueIn(value)
  const unit = unitOf(item.config)

  return (
    <QuestionFrame item={item}>
      {(headingId) => (
        <div className="flex items-center gap-2">
          <Input
            type="number"
            inputMode="decimal"
            data-testid="forms-number"
            aria-labelledby={headingId}
            className="max-w-[10rem]"
            min={boundOf(item.config, "min")}
            max={boundOf(item.config, "max")}
            value={current === null ? "" : String(current)}
            onChange={(e) =>
              onChange(e.target.value === "" ? {} : { value: Number(e.target.value) })
            }
          />
          {unit && (
            <span data-testid="forms-number-unit" className="text-sm text-neutral-600">
              {unit}
            </span>
          )}
        </div>
      )}
    </QuestionFrame>
  )
}

export const numberRenderer: ItemRenderer = {
  Component: NumberItem,
  answerable: true,
  label: labelOf,
  summary: (value, item) => {
    const current = valueIn(value)
    if (current === null) return null
    const unit = unitOf(item.config)
    return unit === null ? String(current) : `${current} ${unit}`
  },
}
