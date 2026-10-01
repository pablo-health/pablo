// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * A sentence that means two different things ("two intakes a week on
 * Tuesdays": a weekly cap, or a cap plus Tuesday hours) is shown as both
 * meanings. The clinician picks one, and only then is it a proposal to
 * confirm. Picking for them would quietly store a rule they did not state.
 */

import type { ParseReading } from "@/types/availability"

interface ReadingChoiceProps {
  question: string | null
  readings: ParseReading[]
  onPick: (reading: ParseReading) => void
}

export function ReadingChoice({ question, readings, onPick }: ReadingChoiceProps) {
  return (
    <div className="space-y-2" data-testid="reading-choice">
      {question && <p className="text-sm text-neutral-800">{question}</p>}
      <div className="grid gap-2 sm:grid-cols-2">
        {readings.map((reading) => (
          <button
            key={reading.label}
            type="button"
            onClick={() => onPick(reading)}
            className="rounded-md border border-neutral-200 p-3 text-left hover:border-primary-300 hover:bg-primary-50"
          >
            <span className="block text-sm font-medium text-neutral-900">{reading.label}</span>
            <ul className="mt-1 space-y-0.5">
              {reading.proposals.map((proposal, index) => (
                <li key={index} className="text-xs text-neutral-600">
                  {proposal.human_summary}
                </li>
              ))}
            </ul>
          </button>
        ))}
      </div>
    </div>
  )
}
