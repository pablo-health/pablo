// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Step one: the chart's name and date of birth, for the patient to confirm.
 *
 * An attestation, not a gate. Saying "something's not right" opens a box to
 * say what, and then the form carries on exactly as before — a patient who
 * cannot get past the first screen because their middle name is spelled
 * oddly has been stopped by a typo. The clinician reads the correction and
 * fixes the record; this form never touches it.
 */

import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import type { IntakeIdentity } from "@/lib/api/patientIntake"
import {
  IDENTITY_CONFIRM,
  IDENTITY_CORRECTIONS_LABEL,
  IDENTITY_CORRECTIONS_NOTE,
  IDENTITY_DENY,
  IDENTITY_HEADING,
} from "./intakeCopy"

const CORRECTIONS_MAX = 2_000

interface IdentityStepProps {
  identity: IntakeIdentity
  /** null until the patient answers; the step's Continue waits on it. */
  confirmed: boolean | null
  onConfirmedChange: (confirmed: boolean) => void
  corrections: string
  onCorrectionsChange: (corrections: string) => void
}

export function IdentityStep({
  identity,
  confirmed,
  onConfirmedChange,
  corrections,
  onCorrectionsChange,
}: IdentityStepProps) {
  return (
    <section aria-labelledby="intake-identity-heading">
      <h2 id="intake-identity-heading" className="text-lg font-semibold text-neutral-900">
        {IDENTITY_HEADING}
      </h2>

      <dl className="mt-4 space-y-2 rounded-md border border-neutral-200 p-4 text-sm">
        <div className="flex justify-between gap-4">
          <dt className="text-neutral-500">Name</dt>
          <dd data-testid="intake-identity-name" className="text-right font-medium text-neutral-900">
            {identity.first_name} {identity.last_name}
          </dd>
        </div>
        {identity.date_of_birth && (
          <div className="flex justify-between gap-4">
            <dt className="text-neutral-500">Date of birth</dt>
            <dd data-testid="intake-identity-dob" className="text-right font-medium text-neutral-900">
              {identity.date_of_birth}
            </dd>
          </div>
        )}
      </dl>

      <div className="mt-4 flex flex-col gap-2">
        <ChoiceButton
          testId="intake-identity-confirm"
          selected={confirmed === true}
          onClick={() => onConfirmedChange(true)}
        >
          {IDENTITY_CONFIRM}
        </ChoiceButton>
        <ChoiceButton
          testId="intake-identity-deny"
          selected={confirmed === false}
          onClick={() => onConfirmedChange(false)}
        >
          {IDENTITY_DENY}
        </ChoiceButton>
      </div>

      {confirmed === false && (
        <div className="mt-4 space-y-1.5">
          <Label htmlFor="intake-corrections">{IDENTITY_CORRECTIONS_LABEL}</Label>
          <Textarea
            id="intake-corrections"
            data-testid="intake-corrections"
            value={corrections}
            maxLength={CORRECTIONS_MAX}
            onChange={(e) => onCorrectionsChange(e.target.value)}
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
