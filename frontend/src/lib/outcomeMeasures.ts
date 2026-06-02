// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * UI-only metadata for scored clinical instruments.
 *
 * The backend (`app.outcome_measures.instruments`) is the source of truth for
 * scoring, validation, and severity bands. This module carries only what the
 * UI needs to *render* an entry form and a trend: instrument labels, the item
 * prompts, the 0–3 response anchors, and presentation concerns (severity badge
 * colors, the PHQ-9 item-9 safety signal). It never computes a total or a
 * severity — those come back from the API.
 *
 * PHQ-9 and GAD-7 are public-domain instruments.
 */

export interface ResponseOption {
  value: number
  label: string
}

export interface InstrumentMeta {
  /** Instrument code sent to the API (matches the backend registry key). */
  code: string
  displayName: string
  /** Ordered item prompts; index 0 is item "1". */
  items: string[]
  /** Shared response scale for every item (PHQ-9/GAD-7 are all 0–3). */
  responseOptions: ResponseOption[]
  /**
   * Optional safety signal: an item whose non-minimal endorsement should be
   * surfaced to the clinician (PHQ-9 item 9 — suicidality).
   */
  safetySignal?: {
    /** Item key (1-based, as string) to watch. */
    itemKey: string
    /** Endorsement at or above this value trips the signal. */
    threshold: number
    /** Short label shown on the row / in the form. */
    label: string
    /** Non-blocking guidance shown beneath the form once tripped. */
    guidance: string
  }
}

/** Standard PHQ-9 / GAD-7 frequency anchors. */
const FREQUENCY_OPTIONS: ResponseOption[] = [
  { value: 0, label: "Not at all" },
  { value: 1, label: "Several days" },
  { value: 2, label: "More than half the days" },
  { value: 3, label: "Nearly every day" },
]

const PHQ9: InstrumentMeta = {
  code: "phq9",
  displayName: "PHQ-9",
  items: [
    "Little interest or pleasure in doing things",
    "Feeling down, depressed, or hopeless",
    "Trouble falling or staying asleep, or sleeping too much",
    "Feeling tired or having little energy",
    "Poor appetite or overeating",
    "Feeling bad about yourself — or that you are a failure or have let yourself or your family down",
    "Trouble concentrating on things, such as reading the newspaper or watching television",
    "Moving or speaking so slowly that other people could have noticed — or the opposite, being so fidgety or restless that you have been moving around a lot more than usual",
    "Thoughts that you would be better off dead, or of hurting yourself in some way",
  ],
  responseOptions: FREQUENCY_OPTIONS,
  safetySignal: {
    itemKey: "9",
    threshold: 1,
    label: "Item 9 endorsed — assess safety",
    guidance:
      "This response indicates possible suicidality. Consider assessing risk directly and following your safety-planning protocol.",
  },
}

const GAD7: InstrumentMeta = {
  code: "gad7",
  displayName: "GAD-7",
  items: [
    "Feeling nervous, anxious, or on edge",
    "Not being able to stop or control worrying",
    "Worrying too much about different things",
    "Trouble relaxing",
    "Being so restless that it is hard to sit still",
    "Becoming easily annoyed or irritable",
    "Feeling afraid, as if something awful might happen",
  ],
  responseOptions: FREQUENCY_OPTIONS,
}

export const INSTRUMENTS: InstrumentMeta[] = [PHQ9, GAD7]

export function getInstrumentMeta(code: string): InstrumentMeta | undefined {
  return INSTRUMENTS.find((i) => i.code === code)
}

/**
 * Tailwind classes for a severity badge, keyed off the server-returned label.
 * Unknown labels fall back to neutral so new backend bands still render.
 */
export function severityBadgeClasses(severity: string | null): string {
  switch (severity) {
    case "minimal":
      return "bg-secondary-100 text-secondary-700"
    case "mild":
      return "bg-yellow-100 text-yellow-800"
    case "moderate":
      return "bg-amber-100 text-amber-800"
    case "moderately severe":
      return "bg-orange-100 text-orange-800"
    case "severe":
      return "bg-red-100 text-red-700"
    default:
      return "bg-neutral-100 text-neutral-700"
  }
}

/**
 * True when an administration trips its instrument's safety signal — i.e. the
 * watched item is present and endorsed at/above the threshold. Pure UI read of
 * the stored item_scores; no clinical inference.
 */
export function tripsSafetySignal(
  meta: InstrumentMeta | undefined,
  itemScores: Record<string, number> | null | undefined,
): boolean {
  if (!meta?.safetySignal || !itemScores) return false
  const value = itemScores[meta.safetySignal.itemKey]
  return typeof value === "number" && value >= meta.safetySignal.threshold
}
