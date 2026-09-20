// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One screener, one step: the stem, then every item as its own radio group.
 *
 * Item wording and the four anchors come from the server's form response and
 * are rendered as given. They are validated instruments — an item reworded
 * on the way to the screen is a different instrument, and its bands stop
 * meaning what the literature says they mean.
 *
 * Native `fieldset`/`legend` + `input[type=radio]` rather than a custom
 * control: a screener on a phone is exactly the case where the platform's
 * own radio behaviour (tap target, arrow keys, screen reader group name) is
 * better than anything hand-rolled.
 *
 * Every item is required. The route rejects a partial screener rather than
 * score it, so Continue waits until the step is complete instead of letting
 * someone reach the end and be turned away.
 */

import type { IntakeInstrument } from "@/lib/api/patientIntake"

interface ScreenerStepProps {
  instrument: IntakeInstrument
  /** Item key -> chosen value. Absent key means unanswered. */
  answers: Record<string, number>
  onAnswer: (itemKey: string, value: number) => void
}

export function ScreenerStep({ instrument, answers, onAnswer }: ScreenerStepProps) {
  const itemKeys = Object.keys(instrument.items)

  return (
    <section aria-labelledby="intake-screener-heading">
      <h2 id="intake-screener-heading" className="text-lg font-semibold text-neutral-900">
        {instrument.display_name}
      </h2>
      <p className="mt-2 text-sm text-neutral-600">{instrument.prompt}</p>

      <div className="mt-5 space-y-5">
        {itemKeys.map((itemKey) => (
          <ScreenerItem
            key={itemKey}
            instrumentCode={instrument.code}
            itemKey={itemKey}
            text={instrument.items[itemKey]}
            options={instrument.response_options}
            selected={answers[itemKey]}
            onAnswer={onAnswer}
          />
        ))}
      </div>
    </section>
  )
}

function ScreenerItem({
  instrumentCode,
  itemKey,
  text,
  options,
  selected,
  onAnswer,
}: {
  instrumentCode: string
  itemKey: string
  text: string
  options: IntakeInstrument["response_options"]
  selected: number | undefined
  onAnswer: (itemKey: string, value: number) => void
}) {
  const groupName = `${instrumentCode}-${itemKey}`
  return (
    <fieldset data-testid={`intake-item-${groupName}`} className="border-0 p-0">
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
