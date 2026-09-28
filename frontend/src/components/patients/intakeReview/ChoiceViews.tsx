// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The questions answered by picking: one, any, yes or no, a point on a scale.
 *
 * Every answer the patient was offered is listed, and the chosen one is
 * marked — the reader sees what was picked and what it was picked from. The
 * answer shapes are read with the portal renderers' own helpers, so the two
 * screens cannot disagree about which option a stored key means.
 */

import { chosenMany, chosenOne, optionsOf } from "@/components/portal/forms/renderers/ChoiceItem"
import {
  anchorOf,
  boundsOf,
  pointsOf,
  valueIn as scaleValueIn,
} from "@/components/portal/forms/renderers/ScaleItem"
import {
  followUpIn,
  followUpLabelOf,
  yesIn,
} from "@/components/portal/forms/renderers/YesNoItem"
import type { ItemView, ItemViewProps } from "./types"
import { NoAnswer, OptionRow, ViewFrame, Written, questionOf } from "./ViewParts"

function SingleChoiceView({ item }: ItemViewProps) {
  const chosen = chosenOne(item.value)
  return (
    <ViewFrame heading={questionOf(item)} helpText={item.help_text}>
      <div role="radiogroup" aria-label={questionOf(item)} className="flex flex-col gap-1.5">
        {optionsOf(item.config).map((option) => (
          <OptionRow key={option.key} kind="radio" name={`view-${item.id}`}
            label={option.label} chosen={chosen === option.key} />
        ))}
      </div>
      {chosen === null && <NoAnswer />}
    </ViewFrame>
  )
}

function MultiChoiceView({ item }: ItemViewProps) {
  const chosen = chosenMany(item.value)
  return (
    <ViewFrame heading={questionOf(item)} helpText={item.help_text}>
      <div role="group" aria-label={questionOf(item)} className="flex flex-col gap-1.5">
        {optionsOf(item.config).map((option) => (
          <OptionRow key={option.key} kind="checkbox" name={`view-${item.id}-${option.key}`}
            label={option.label} chosen={chosen.includes(option.key)} />
        ))}
      </div>
      {item.value === null && <NoAnswer />}
    </ViewFrame>
  )
}

function YesNoView({ item }: ItemViewProps) {
  const yes = yesIn(item.value)
  const followUpLabel = followUpLabelOf(item.config)
  const followUp = followUpIn(item.value).trim()
  return (
    <ViewFrame heading={questionOf(item)} helpText={item.help_text}>
      <div role="radiogroup" aria-label={questionOf(item)} className="flex flex-col gap-1.5">
        <OptionRow kind="radio" name={`view-${item.id}`} label="Yes" chosen={yes === true} />
        <OptionRow kind="radio" name={`view-${item.id}`} label="No" chosen={yes === false} />
      </div>
      {yes === null && <NoAnswer />}
      {yes === true && followUpLabel !== null && followUp !== "" && (
        <div className="mt-2 space-y-1">
          <p className="text-sm text-neutral-700">{followUpLabel}</p>
          <Written text={followUp} />
        </div>
      )}
    </ViewFrame>
  )
}

function ScaleView({ item }: ItemViewProps) {
  const bounds = boundsOf(item.config)
  const chosen = scaleValueIn(item.value)
  if (bounds === null) {
    return (
      <ViewFrame heading={questionOf(item)} helpText={item.help_text}>
        {chosen === null ? <NoAnswer /> : <Written text={String(chosen)} />}
      </ViewFrame>
    )
  }
  return (
    <ViewFrame heading={questionOf(item)} helpText={item.help_text}>
      <div role="radiogroup" aria-label={questionOf(item)} className="flex flex-wrap gap-1.5">
        {pointsOf(bounds).map((point) => (
          <label key={point} data-chosen={chosen === point || undefined}
            className={
              chosen === point
                ? "flex h-10 w-10 items-center justify-center rounded-md border border-primary-400 bg-primary-50 text-sm font-medium text-primary-800"
                : "flex h-10 w-10 items-center justify-center rounded-md border border-neutral-200 text-sm text-neutral-600"
            }>
            <input type="radio" name={`view-${item.id}`} checked={chosen === point}
              disabled readOnly className="sr-only" />
            {point}
          </label>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-xs text-neutral-500">
        <span>{anchorOf(item.config, "min_label")}</span>
        <span>{anchorOf(item.config, "max_label")}</span>
      </div>
      {chosen === null && <NoAnswer />}
    </ViewFrame>
  )
}

export const singleChoiceView: ItemView = { Component: SingleChoiceView, answerable: true }
export const multiChoiceView: ItemView = { Component: MultiChoiceView, answerable: true }
export const yesNoView: ItemView = { Component: YesNoView, answerable: true }
export const scaleView: ItemView = { Component: ScaleView, answerable: true }
