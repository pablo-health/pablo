// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { pabloNoteExtensions } from "./pabloNoteLines.extensions"

/**
 * The lines Pablo's note in the sidebar rotates through, and the extension
 * point for them.
 *
 * Every line names something real in the product that the reader can act on
 * in one step, in Pablo's voice. A line about a feature this deployment does
 * not run carries a gate and is dropped while that feature is off, so the note
 * never points at a screen that is not there.
 *
 * The base list lives here; a downstream build may append lines, or hide a base
 * line by its id, through the `pabloNoteLines.extensions.ts` *merge slot* rather
 * than overwriting this file or forking `PabloNote.tsx` — the same merge-slot
 * discipline as `sidebarExtensions.ts`. Gates are answered by
 * `usePabloNoteVisibility` (`pabloNoteVisibility.ts`).
 */
export interface PabloNoteLine {
  /** Stable identity across builds; what `hide` names. */
  id: string
  /** May hold `{people}`-style placeholders; rendered through `sayPeople`. */
  text: string
  /**
   * A feature that must be on for the line to show. Either a name from the
   * deployment's feature list (see `useFeature`) or one of the runtime toggles
   * in `pabloNoteVisibility.ts` (`public_booking`, `google_calendar`,
   * `passkeys`, `companion`).
   */
  requiresFlag?: string
  /**
   * A feature that hides the line while it is on, answered the same way as
   * `requiresFlag`. Set through the merge slot's `hideWhenOn`, for a line
   * that a build says another way when that feature is on.
   */
  hiddenWhenFlag?: string
  /**
   * A plan capability the line needs. The base build has no notion of plans
   * and ignores it; a downstream build answers it in `pabloNoteVisibility.ts`.
   */
  requiresCapability?: string
}

/**
 * Shape of the merge slot. Declared here in the stable file so a replacement
 * slot can import the type (it cannot import it from the file it replaces).
 *
 * - `append`: lines added after the base lines, in slot order.
 * - `hide`: ids of base lines to drop.
 * - `hideWhenOn`: base line id → a feature that hides that line while the
 *   feature is on. The line still shows wherever the feature is off.
 */
export interface PabloNoteExtensions {
  append: PabloNoteLine[]
  hide: string[]
  hideWhenOn?: Record<string, string>
}

const baseLines: PabloNoteLine[] = [
  {
    id: "compliance-renewals",
    text: "Add CAQH or a license renewal in Settings > Compliance and I’ll show it on your dashboard as it comes due.",
  },
  { id: "drag-appointment", text: "Drag an appointment to move it; double-click to change it." },
  {
    id: "self-booking",
    text: "{People} can book themselves from your page. It’s in Settings, under Scheduling.",
    requiresFlag: "public_booking",
  },
  { id: "note-types-order", text: "Settings > Note types changes what I draft and in what order." },
  {
    id: "simplepractice-import",
    text: "Bring a SimplePractice export any time; I’ll match what’s already here.",
  },
  {
    id: "companion-start",
    text: "Start session opens Pablo on your desktop; the note draft comes back here.",
    requiresFlag: "companion",
  },
  { id: "review-sign", text: "Notes wait in Review until you sign them." },
  {
    id: "intake-packet",
    text: "Your intake packet goes out when a {person} books. Settings > {Person} portal decides what’s in it.",
    requiresFlag: "patient_portal",
  },
  {
    id: "measures",
    text: "Send a PHQ-9 or GAD-7 from a {person}’s chart; the score lands on the chart when they finish.",
    requiresFlag: "patient_portal",
  },
  {
    id: "google-calendar",
    text: "Connect Google Calendar in Settings > Calendars and your appointments show here.",
    requiresFlag: "google_calendar",
  },
  {
    id: "inbox-drafts",
    text: "A {person} email with a time in it gets a drafted reply in your voice. You send it.",
    requiresFlag: "practice_inbox",
  },
  {
    id: "people-term",
    // people-term-ok: the line names both choices; it is the setting itself.
    text: "Say “clients” or “patients” once in Settings > Profile and every screen follows.",
  },
  {
    id: "superbills",
    text: "A superbill for any date range is in Settings, under Superbills & rates.",
    requiresFlag: "superbills",
  },
  {
    id: "passkeys",
    text: "Passkeys sign you in without a code. Settings > Sign-in & security.",
    requiresFlag: "passkeys",
  },
  {
    id: "own-note-type",
    text: "A note type you’ve changed is yours; add one from Settings > Note types.",
  },
]

/**
 * Base lines minus the hidden ones, each carrying the feature that hides it
 * while on (if the slot names one), then the slot's appended lines.
 */
export function mergeNoteLines(base: PabloNoteLine[], extensions: PabloNoteExtensions): PabloNoteLine[] {
  const hidden = new Set(extensions.hide)
  const hideWhenOn = extensions.hideWhenOn ?? {}
  const kept = base
    .filter((line) => !hidden.has(line.id))
    .map((line) => (line.id in hideWhenOn ? { ...line, hiddenWhenFlag: hideWhenOn[line.id] } : line))
  return [...kept, ...extensions.append]
}

/** Every line this build knows about, before gating. */
export function pabloNoteLines(): PabloNoteLine[] {
  return mergeNoteLines(baseLines, pabloNoteExtensions)
}

/** Days since the epoch, in UTC: the rotation's clock. */
export function dayNumber(now: number = Date.now()): number {
  return Math.floor(now / 86_400_000)
}

/**
 * The line for a given day. Deterministic, so the server render and the
 * client agree.
 *
 * The day picks a slot in the full list and the first line from there that
 * passes its gates is shown. Picking over the full list rather than the passing
 * ones means a gate that resolves late (the desktop check runs only in the
 * browser) changes the line only on a day whose own line was gated.
 */
export function pickNoteLine(
  lines: PabloNoteLine[],
  isVisible: (line: PabloNoteLine) => boolean,
  day: number,
): PabloNoteLine | undefined {
  const count = lines.length
  if (count === 0) return undefined
  const start = ((day % count) + count) % count
  for (let offset = 0; offset < count; offset++) {
    const line = lines[(start + offset) % count]
    if (isVisible(line)) return line
  }
  return undefined
}
