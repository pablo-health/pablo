// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The chart's name and date of birth, for the patient to confirm.
 *
 * An attestation, not a gate. Saying "something's not right" opens a box to
 * say what, and then the form carries on exactly as before — a patient who
 * cannot get past a screen because their middle name is spelled oddly has
 * been stopped by a typo. The clinician reads the correction and fixes the
 * record; this form never touches it.
 *
 * The answer is `{name_confirmed, dob_confirmed, corrections}` because that
 * is what the save route stores. One tap sets both flags: the screen asks
 * one question — is this you — rather than auditing the record field by
 * field. They stay separate on the wire because that is the route's shape
 * and a later screen may split the question.
 */

import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  IDENTITY_CONFIRM,
  IDENTITY_CORRECTIONS_LABEL,
  IDENTITY_CORRECTIONS_NOTE,
  IDENTITY_DENY,
  IDENTITY_DOB_LABEL,
  IDENTITY_HEADING,
  IDENTITY_NAME_LABEL,
  IDENTITY_SUMMARY_CONFIRMED,
  IDENTITY_SUMMARY_FLAGGED,
} from "../formsCopy"
import type { AnswerValue, ItemRenderer, ItemRendererProps } from "./types"

/** Matches the route's own cap on a correction. */
const CORRECTIONS_MAX = 4_000

function confirmedIn(value: AnswerValue | null): boolean | null {
  const flag = value?.name_confirmed
  return typeof flag === "boolean" ? flag : null
}

function correctionsIn(value: AnswerValue | null): string {
  const text = value?.corrections
  return typeof text === "string" ? text : ""
}

function DemographicsItem({ value, onChange, form }: ItemRendererProps) {
  const confirmed = confirmedIn(value)
  const corrections = correctionsIn(value)
  const identity = form?.identity ?? null

  const answer = (nextConfirmed: boolean, nextCorrections: string): AnswerValue => ({
    name_confirmed: nextConfirmed,
    dob_confirmed: nextConfirmed,
    corrections: nextCorrections.trim() === "" ? null : nextCorrections,
  })

  return (
    <section aria-labelledby="forms-identity-heading">
      <h2 id="forms-identity-heading" className="text-lg font-semibold text-neutral-900">
        {IDENTITY_HEADING}
      </h2>

      {identity && (
        <dl className="mt-4 space-y-2 rounded-md border border-neutral-200 p-4 text-sm">
          <div className="flex justify-between gap-4">
            <dt className="text-neutral-500">{IDENTITY_NAME_LABEL}</dt>
            <dd
              data-testid="forms-identity-name"
              className="text-right font-medium text-neutral-900"
            >
              {identity.first_name} {identity.last_name}
            </dd>
          </div>
          {identity.date_of_birth && (
            <div className="flex justify-between gap-4">
              <dt className="text-neutral-500">{IDENTITY_DOB_LABEL}</dt>
              <dd
                data-testid="forms-identity-dob"
                className="text-right font-medium text-neutral-900"
              >
                {identity.date_of_birth}
              </dd>
            </div>
          )}
        </dl>
      )}

      <div className="mt-4 flex flex-col gap-2">
        <ChoiceButton
          testId="forms-identity-confirm"
          selected={confirmed === true}
          onClick={() => onChange(answer(true, corrections))}
        >
          {IDENTITY_CONFIRM}
        </ChoiceButton>
        <ChoiceButton
          testId="forms-identity-deny"
          selected={confirmed === false}
          onClick={() => onChange(answer(false, corrections))}
        >
          {IDENTITY_DENY}
        </ChoiceButton>
      </div>

      {confirmed === false && (
        <div className="mt-4 space-y-1.5">
          <Label htmlFor="forms-corrections">{IDENTITY_CORRECTIONS_LABEL}</Label>
          <Textarea
            id="forms-corrections"
            data-testid="forms-corrections"
            value={corrections}
            maxLength={CORRECTIONS_MAX}
            onChange={(e) => onChange(answer(false, e.target.value))}
          />
          <p className="text-xs text-neutral-500">{IDENTITY_CORRECTIONS_NOTE}</p>
        </div>
      )}
    </section>
  )
}

function ChoiceButton({
  children,
  selected,
  onClick,
  testId,
}: {
  children: React.ReactNode
  selected: boolean
  onClick: () => void
  testId: string
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      aria-pressed={selected}
      onClick={onClick}
      className={
        selected
          ? "rounded-md border border-primary-400 bg-primary-50 px-4 py-3 text-left text-sm font-medium text-primary-800"
          : "rounded-md border border-neutral-200 px-4 py-3 text-left text-sm text-neutral-700 hover:border-neutral-300"
      }
    >
      {children}
    </button>
  )
}

export const demographicsRenderer: ItemRenderer = {
  Component: DemographicsItem,
  answerable: true,
  label: () => IDENTITY_HEADING,
  // The correction itself is the patient's own words about their record, and
  // the review screen is a list somebody may read over a shoulder on a bus.
  // Whether they flagged something is enough to know the question is done.
  summary: (value) => {
    const confirmed = confirmedIn(value)
    if (confirmed === null) return null
    return confirmed ? IDENTITY_SUMMARY_CONFIRMED : IDENTITY_SUMMARY_FLAGGED
  },
}
