// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Pick one, and pick any: the same list of answers, one control apart.
 *
 * Both store the option's `key` rather than its label — the key is what a
 * later question's visibility rule compares against, so an answer survives
 * the practice fixing a typo in the wording. `{key}` for one, `{keys: []}`
 * for any, which is what `backend/app/intake/answers.py` validates.
 *
 * Native radios and checkboxes rather than custom controls: a list of
 * answers on a phone is where the platform's own tap target, keyboard
 * behaviour and group naming are hardest to beat by hand.
 *
 * How many an answer may hold is the server's to refuse. Nothing here caps
 * the boxes at `max`, because a control that silently stops responding is a
 * worse answer to "you have picked too many" than a sentence saying so.
 */

import { QuestionFrame, labelOf } from "./QuestionFrame"
import type { AnswerValue, ItemRenderer, ItemRendererProps } from "./types"

interface Option {
  key: string
  label: string
}

/** The answers this question offers, as the editor stored them. */
export function optionsOf(config: Record<string, unknown>): Option[] {
  if (!Array.isArray(config.options)) return []
  return config.options.filter(
    (option): option is Option =>
      typeof option === "object" &&
      option !== null &&
      typeof (option as Option).key === "string" &&
      typeof (option as Option).label === "string",
  )
}

function labelsFor(config: Record<string, unknown>, keys: string[]): string[] {
  const options = optionsOf(config)
  return keys.map((key) => options.find((option) => option.key === key)?.label ?? key)
}

function chosenOne(value: AnswerValue | null): string | null {
  return typeof value?.key === "string" ? value.key : null
}

function chosenMany(value: AnswerValue | null): string[] {
  if (!Array.isArray(value?.keys)) return []
  return value.keys.filter((key): key is string => typeof key === "string")
}

function SingleChoiceItem({ item, value, onChange }: ItemRendererProps) {
  const options = optionsOf(item.config)
  const chosen = chosenOne(value)
  return (
    <QuestionFrame item={item}>
      {() => (
        <div role="radiogroup" data-testid="forms-single-choice" className="flex flex-col gap-1.5">
          {options.map((option) => (
            <OptionRow
              key={option.key}
              kind="radio"
              name={`forms-choice-${item.id}`}
              option={option}
              itemId={item.id}
              active={chosen === option.key}
              onPick={() => onChange({ key: option.key })}
            />
          ))}
        </div>
      )}
    </QuestionFrame>
  )
}

function MultiChoiceItem({ item, value, onChange }: ItemRendererProps) {
  const options = optionsOf(item.config)
  const chosen = chosenMany(value)

  const toggle = (key: string) =>
    onChange({
      keys: chosen.includes(key) ? chosen.filter((k) => k !== key) : [...chosen, key],
    })

  return (
    <QuestionFrame item={item}>
      {() => (
        <div data-testid="forms-multi-choice" className="flex flex-col gap-1.5">
          {options.map((option) => (
            <OptionRow
              key={option.key}
              kind="checkbox"
              name={`forms-choice-${item.id}-${option.key}`}
              option={option}
              itemId={item.id}
              active={chosen.includes(option.key)}
              onPick={() => toggle(option.key)}
            />
          ))}
        </div>
      )}
    </QuestionFrame>
  )
}

function OptionRow({
  kind,
  name,
  option,
  itemId,
  active,
  onPick,
}: {
  kind: "radio" | "checkbox"
  name: string
  option: Option
  itemId: string
  active: boolean
  onPick: () => void
}) {
  const inputId = `forms-choice-${itemId}-${option.key}`
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
        type={kind}
        id={inputId}
        name={name}
        checked={active}
        onChange={onPick}
        className="h-4 w-4"
      />
      {option.label}
    </label>
  )
}

export const singleChoiceRenderer: ItemRenderer = {
  Component: SingleChoiceItem,
  answerable: true,
  label: labelOf,
  summary: (value, item) => {
    const chosen = chosenOne(value)
    return chosen === null ? null : labelsFor(item.config, [chosen])[0]
  },
}

export const multiChoiceRenderer: ItemRenderer = {
  Component: MultiChoiceItem,
  answerable: true,
  label: labelOf,
  summary: (value, item) => {
    const chosen = chosenMany(value)
    return chosen.length === 0 ? null : labelsFor(item.config, chosen).join(", ")
  },
}
