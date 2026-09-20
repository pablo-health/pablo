// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Yes or no, and the box a yes may open.
 *
 * The answer is `{yes}`, plus `{follow_up}` when the practice attached one
 * and the answer is yes. The box appears on a yes and is left alone on a no:
 * the save route ignores a follow-up sitting behind a no, so somebody who
 * changes their mind after typing does not have to clear it to get past the
 * question.
 */

import { Textarea } from "@/components/ui/textarea"
import { QuestionFrame, labelOf } from "./QuestionFrame"
import type { AnswerValue, ItemRenderer, ItemRendererProps } from "./types"

/** Matches `REASON_MAX_LEN`, which is what the route caps a follow-up at. */
const FOLLOW_UP_MAX = 4_000

function followUpLabelOf(config: Record<string, unknown>): string | null {
  const label = typeof config.follow_up_label === "string" ? config.follow_up_label.trim() : ""
  return label === "" ? null : label
}

function yesIn(value: AnswerValue | null): boolean | null {
  return typeof value?.yes === "boolean" ? value.yes : null
}

function followUpIn(value: AnswerValue | null): string {
  return typeof value?.follow_up === "string" ? value.follow_up : ""
}

function YesNoItem({ item, value, onChange }: ItemRendererProps) {
  const yes = yesIn(value)
  const followUpLabel = followUpLabelOf(item.config)
  const followUp = followUpIn(value)

  const answer = (next: boolean) =>
    onChange(followUp === "" ? { yes: next } : { yes: next, follow_up: followUp })

  return (
    <QuestionFrame item={item}>
      {() => (
        <div className="space-y-3">
          <div role="radiogroup" data-testid="forms-yes-no" className="flex flex-col gap-1.5">
            <Choice itemId={item.id} label="Yes" active={yes === true} onPick={() => answer(true)} />
            <Choice itemId={item.id} label="No" active={yes === false} onPick={() => answer(false)} />
          </div>

          {yes === true && followUpLabel !== null && (
            <div className="space-y-1.5">
              <label
                htmlFor={`forms-follow-up-${item.id}`}
                className="block text-sm text-neutral-800"
              >
                {followUpLabel}
              </label>
              <Textarea
                id={`forms-follow-up-${item.id}`}
                data-testid="forms-yes-no-follow-up"
                value={followUp}
                maxLength={FOLLOW_UP_MAX}
                onChange={(e) => onChange({ yes: true, follow_up: e.target.value })}
              />
            </div>
          )}
        </div>
      )}
    </QuestionFrame>
  )
}

function Choice({
  itemId,
  label,
  active,
  onPick,
}: {
  itemId: string
  label: string
  active: boolean
  onPick: () => void
}) {
  const inputId = `forms-yes-no-${itemId}-${label.toLowerCase()}`
  return (
    <label
      htmlFor={inputId}
      className={
        active
          ? "flex items-center gap-3 rounded-md border border-primary-400 bg-primary-50 px-3 py-2.5 text-sm font-medium text-primary-800"
          : "flex items-center gap-3 rounded-md border border-neutral-200 px-3 py-2.5 text-sm text-neutral-700"
      }
    >
      <input
        type="radio"
        id={inputId}
        name={`forms-yes-no-${itemId}`}
        checked={active}
        onChange={onPick}
        className="h-4 w-4"
      />
      {label}
    </label>
  )
}

export const yesNoRenderer: ItemRenderer = {
  Component: YesNoItem,
  answerable: true,
  label: labelOf,
  summary: (value) => {
    const yes = yesIn(value)
    if (yes === null) return null
    const followUp = followUpIn(value).trim()
    return yes && followUp !== "" ? `Yes — ${followUp}` : yes ? "Yes" : "No"
  },
}
