// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A measure, item by item, with the answer the patient chose on each.
 *
 * The item text and the anchors are the server's — the same registry the
 * portal asked from, sent with the review — so what the chart shows beside
 * an answer is the wording the answer was given against. A validated
 * instrument reworded on the way to the screen would be a different one.
 *
 * **No total and no band.** Those were scored onto the chart's outcome
 * measures when the form arrived, and the chart trends and bands them there.
 * A second total here would be the same score on screen twice, from two
 * sources free to disagree. What this adds is the one thing the trend does
 * not show: which answer was given to which question.
 */

import { instrumentFor, scoresIn } from "@/components/portal/forms/renderers/InstrumentItem"
import type { ItemView, ItemViewProps } from "./types"
import { NoAnswer, ViewFrame, questionOf } from "./ViewParts"

function InstrumentView({ item, form }: ItemViewProps) {
  const instrument = instrumentFor(item.config, form)
  const scores = scoresIn(item.value)

  // A measure this deployment has no wording for. The scores are still the
  // patient's, so they are listed by item number rather than dropped.
  if (instrument === null) {
    const entries = Object.entries(scores)
    return (
      <ViewFrame heading={questionOf(item)}>
        {entries.length === 0 ? (
          <NoAnswer />
        ) : (
          <p className="text-sm text-neutral-900">
            {entries.map(([key, score]) => `${key}: ${score}`).join(" · ")}
          </p>
        )}
      </ViewFrame>
    )
  }

  return (
    <ViewFrame heading={instrument.display_name} helpText={instrument.prompt}>
      <div className="space-y-4">
        {Object.entries(instrument.items).map(([itemKey, text]) => {
          const selected = scores[itemKey]
          const groupName = `view-${item.id}-${itemKey}`
          return (
            <fieldset key={itemKey} className="border-0 p-0"
              data-testid={`intake-view-instrument-${instrument.code}-${itemKey}`}>
              <legend className="text-sm text-neutral-800">{text}</legend>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {instrument.response_options.map((option) => {
                  const chosen = selected === option.value
                  return (
                    <label key={option.value} data-chosen={chosen || undefined}
                      className={
                        chosen
                          ? "flex items-center gap-2 rounded-md border border-primary-400 bg-primary-50 px-2.5 py-1.5 text-xs font-medium text-primary-800"
                          : "flex items-center gap-2 rounded-md border border-neutral-200 px-2.5 py-1.5 text-xs text-neutral-600"
                      }>
                      <input type="radio" name={groupName} checked={chosen} disabled readOnly
                        className="h-3.5 w-3.5" />
                      {option.label}
                    </label>
                  )
                })}
              </div>
              {selected === undefined && item.value !== null && <NoAnswer />}
            </fieldset>
          )
        })}
      </div>
      {item.value === null && <NoAnswer />}
    </ViewFrame>
  )
}

export const instrumentView: ItemView = { Component: InstrumentView, answerable: true }
