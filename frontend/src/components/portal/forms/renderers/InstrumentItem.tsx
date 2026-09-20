// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One measure: the stem, then every item as its own radio group.
 *
 * Item wording and the anchors come from the server and are rendered as
 * given. These are validated instruments — an item reworded on the way to
 * the screen is a different instrument, and its bands stop meaning what the
 * literature says they mean.
 *
 * Native `fieldset`/`legend` + `input[type=radio]` rather than a custom
 * control: a measure on a phone is exactly the case where the platform's own
 * radio behaviour (tap target, arrow keys, screen reader group name) beats
 * anything hand-rolled.
 *
 * The answer is `{item_scores: {"1": 0, …}}` because that is what the save
 * route stores and what the registry scores. A measure with an item
 * unanswered has no total, so the route refuses a partial one and says which
 * item is outstanding.
 *
 * A measure whose wording this deployment did not send back cannot be asked:
 * there is nothing to put on the screen. That reads as a step to come rather
 * than as an error, and the server still counts it outstanding — see the
 * registry for why that is the honest shape.
 */

import type { IntakeForm, IntakeInstrument } from "@/lib/api/patientIntake"
import { ITEM_UNAVAILABLE } from "../formsCopy"
import type { AnswerValue, ItemRenderer, ItemRendererProps } from "./types"

/** The measure this item asks for, by the code the editor stored. */
function codeOf(config: Record<string, unknown>): string | null {
  return typeof config.code === "string" ? config.code : null
}

function instrumentFor(
  config: Record<string, unknown>,
  form: IntakeForm | null,
): IntakeInstrument | null {
  const code = codeOf(config)
  if (code === null || form === null) return null
  return form.instruments.find((candidate) => candidate.code === code) ?? null
}

function scoresIn(value: AnswerValue | null): Record<string, number> {
  const scores = value?.item_scores
  if (typeof scores !== "object" || scores === null) return {}
  return Object.fromEntries(
    Object.entries(scores as Record<string, unknown>).filter(
      (entry): entry is [string, number] => typeof entry[1] === "number",
    ),
  )
}

function InstrumentItem({ item, value, onChange, form }: ItemRendererProps) {
  const instrument = instrumentFor(item.config, form)
  const scores = scoresIn(value)

  if (instrument === null) {
    return (
      <section data-testid="forms-item-unavailable" className="py-4">
        <p className="text-sm text-neutral-600">{ITEM_UNAVAILABLE}</p>
      </section>
    )
  }

  const answer = (itemKey: string, score: number) =>
    onChange({ item_scores: { ...scores, [itemKey]: score } })

  return (
    <section aria-labelledby="forms-instrument-heading">
      <h2 id="forms-instrument-heading" className="text-lg font-semibold text-neutral-900">
        {instrument.display_name}
      </h2>
      <p className="mt-2 text-sm text-neutral-600">{instrument.prompt}</p>

      <div className="mt-5 space-y-5">
        {Object.keys(instrument.items).map((itemKey) => (
          <InstrumentQuestion
            key={itemKey}
            code={instrument.code}
            itemKey={itemKey}
            text={instrument.items[itemKey]}
            options={instrument.response_options}
            selected={scores[itemKey]}
            onAnswer={answer}
          />
        ))}
      </div>
    </section>
  )
}

function InstrumentQuestion({
  code,
  itemKey,
  text,
  options,
  selected,
  onAnswer,
}: {
  code: string
  itemKey: string
  text: string
  options: IntakeInstrument["response_options"]
  selected: number | undefined
  onAnswer: (itemKey: string, score: number) => void
}) {
  const groupName = `${code}-${itemKey}`
  return (
    <fieldset data-testid={`forms-item-${groupName}`} className="border-0 p-0">
      <legend className="text-sm text-neutral-800">{text}</legend>
      <div className="mt-2 flex flex-col gap-1.5">
        {options.map((option) => {
          const inputId = `${groupName}-${option.value}`
          const active = selected === option.value
          return (
            <label
              key={option.value}
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
                name={groupName}
                value={option.value}
                checked={active}
                onChange={() => onAnswer(itemKey, option.value)}
                className="h-4 w-4"
              />
              {option.label}
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}

export const instrumentRenderer: ItemRenderer = {
  Component: InstrumentItem,
  answerable: true,
  crisisFooter: true,
  label: (item, form) =>
    instrumentFor(item.config, form)?.display_name ?? codeOf(item.config) ?? "",
  /**
   * How many of the measure's questions have an answer, and nothing else.
   *
   * Never the total and never a band. Reading a measure is clinical work,
   * and a number on a review screen is the first step towards a patient
   * interpreting it alone.
   */
  summary: (value, item, form) => {
    const instrument = instrumentFor(item.config, form)
    if (instrument === null) return null
    const answered = Object.keys(scoresIn(value)).length
    const total = Object.keys(instrument.items).length
    return answered === 0 ? null : `${answered} of ${total} answered`
  },
}
