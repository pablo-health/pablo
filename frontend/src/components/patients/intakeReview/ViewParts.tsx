// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The pieces every read-only view is built from.
 *
 * The question frame and the option row look like the portal's, so a
 * clinician reads the form in the shape the patient met it: the same
 * heading, the same list of answers, the chosen one marked. The controls
 * are real inputs, disabled — a screen reader announces "checked" on the
 * answer that was picked, and nothing can change it.
 */

import type { ReactNode } from "react"

import type { IntakeReviewItem } from "@/lib/api/intakeReview"

export const VIEW_COPY = {
  noAnswer: "No answer",
  open: "Open",
  opening: "Opening…",
  openFailed: "That file could not be opened. Try again.",
  noFiles: "No files sent.",
  showDocument: "Show document",
  hideDocument: "Hide document",
  documentFailed: "The document could not be loaded.",
  signedAs: (role: string) => `signed as ${role}`,
}

const ACTIVE =
  "flex items-center gap-3 rounded-md border border-primary-400 bg-primary-50 px-3 py-2 text-sm font-medium text-primary-800"
const IDLE =
  "flex items-center gap-3 rounded-md border border-neutral-200 px-3 py-2 text-sm text-neutral-600"

/** The practice's wording for a question, or its key when it had none. */
export function questionOf(item: IntakeReviewItem): string {
  return item.label?.trim() || item.key
}

/** The question and its help text above whatever the answer looks like. */
export function ViewFrame({
  heading,
  helpText,
  children,
}: {
  heading: string
  helpText?: string | null
  children: ReactNode
}) {
  return (
    <div>
      <p className="text-sm font-medium text-neutral-900">{heading}</p>
      {helpText?.trim() && <p className="mt-0.5 text-xs text-neutral-500">{helpText.trim()}</p>}
      <div className="mt-2">{children}</div>
    </div>
  )
}

/** One answer on a list, marked when it was the one chosen. */
export function OptionRow({
  kind,
  name,
  label,
  chosen,
}: {
  kind: "radio" | "checkbox"
  name: string
  label: string
  chosen: boolean
}) {
  return (
    <label className={chosen ? ACTIVE : IDLE} data-chosen={chosen || undefined}>
      <input type={kind} name={name} checked={chosen} disabled readOnly className="h-4 w-4" />
      {label}
    </label>
  )
}

/** Something the patient typed, shown as text rather than in a live box. */
export function Written({ text, testId }: { text: string; testId?: string }) {
  return (
    <p
      data-testid={testId}
      className="whitespace-pre-wrap rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-900"
    >
      {text}
    </p>
  )
}

/** The line a question with nothing recorded gets. */
export function NoAnswer() {
  return <p className="text-sm text-neutral-500">{VIEW_COPY.noAnswer}</p>
}
