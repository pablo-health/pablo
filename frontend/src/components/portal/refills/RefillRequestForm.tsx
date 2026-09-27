// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Ask for a refill: pick a medication (or name one), optionally a
 * pharmacy, optionally a note.
 *
 * The request names exactly one of a listed medication or a typed name —
 * the route rejects both or neither — so "Something else" is a choice in
 * the same radio group rather than a separate field that could be filled
 * alongside a pick. With no listed medications there is nothing to choose
 * between, and the typed name is the whole question.
 *
 * The crisis line sits above the note, because the note is the one place
 * a patient might write something urgent. The sentence is the messaging
 * notice's own, imported rather than restated, so the two can never drift.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  MEDICATION_TEXT_MAX,
  PATIENT_NOTE_MAX,
  PHARMACY_TEXT_MAX,
  type CreateRefillRequestInput,
  type PatientRefillMedication,
} from "@/lib/api/patientRefills"
import { CRISIS_LINE } from "../messaging/ExpectationNotice"
import {
  FORM_HEADING,
  MEDICATION_LEGEND,
  MEDICATION_NAME_LABEL,
  NOTE_LABEL,
  PHARMACY_LABEL,
  SOMETHING_ELSE,
  SUBMIT,
  SUBMITTING,
} from "./refillsCopy"

/** The radio value for "Something else". Not a medication id the server can issue. */
const OTHER = "__other__"

export interface RefillRequestFormProps {
  medications: PatientRefillMedication[]
  onSubmit: (input: CreateRefillRequestInput) => Promise<unknown>
  submitting: boolean
  error?: string | null
}

function orNull(value: string): string | null {
  const trimmed = value.trim()
  return trimmed ? trimmed : null
}

export function RefillRequestForm({
  medications,
  onSubmit,
  submitting,
  error,
}: RefillRequestFormProps) {
  const hasList = medications.length > 0
  const [choice, setChoice] = useState<string | null>(null)
  const [medicationText, setMedicationText] = useState("")
  const [pharmacy, setPharmacy] = useState("")
  const [note, setNote] = useState("")

  // Without a list, the typed name is the only way to say what is wanted.
  const typing = !hasList || choice === OTHER
  const chosen = typing ? medicationText.trim().length > 0 : choice !== null
  const canSubmit = chosen && !submitting

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canSubmit) return
    const medication: CreateRefillRequestInput = typing
      ? { medication_id: null, medication_text: medicationText.trim() }
      : { medication_id: choice, medication_text: null }
    try {
      await onSubmit({
        ...medication,
        pharmacy_text: orNull(pharmacy),
        patient_note: orNull(note),
      })
      setChoice(null)
      setMedicationText("")
      setPharmacy("")
      setNote("")
    } catch {
      // The caller renders the failure; what the patient entered stays put.
    }
  }

  const nameInput = (
    <div>
      <Label htmlFor="portal-refills-medication-text">{MEDICATION_NAME_LABEL}</Label>
      <Input
        id="portal-refills-medication-text"
        data-testid="portal-refills-medication-text"
        value={medicationText}
        onChange={(event) => setMedicationText(event.target.value)}
        maxLength={MEDICATION_TEXT_MAX}
        disabled={submitting}
        className="mt-1"
      />
    </div>
  )

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-3"
      data-testid="portal-refills-form"
      aria-labelledby="portal-refills-form-heading"
    >
      <h3
        id="portal-refills-form-heading"
        className="text-sm font-semibold text-neutral-900"
      >
        {FORM_HEADING}
      </h3>

      {hasList ? (
        <fieldset className="flex flex-col gap-2" data-testid="portal-refills-medications">
          <legend className="mb-1 text-sm font-medium text-neutral-900">
            {MEDICATION_LEGEND}
          </legend>
          {medications.map((medication) => (
            <label
              key={medication.id}
              htmlFor={`portal-refills-medication-${medication.id}`}
              className="flex items-center gap-2 text-sm text-neutral-900"
            >
              <input
                type="radio"
                id={`portal-refills-medication-${medication.id}`}
                data-testid={`portal-refills-medication-${medication.id}`}
                name="portal-refills-medication"
                value={medication.id}
                checked={choice === medication.id}
                onChange={() => setChoice(medication.id)}
                disabled={submitting}
                className="h-4 w-4"
              />
              {`${medication.drug_name} ${medication.dose}`.trim()}
            </label>
          ))}
          <label
            htmlFor="portal-refills-medication-other"
            className="flex items-center gap-2 text-sm text-neutral-900"
          >
            <input
              type="radio"
              id="portal-refills-medication-other"
              data-testid="portal-refills-medication-other"
              name="portal-refills-medication"
              value={OTHER}
              checked={choice === OTHER}
              onChange={() => setChoice(OTHER)}
              disabled={submitting}
              className="h-4 w-4"
            />
            {SOMETHING_ELSE}
          </label>
          {typing && <div className="pl-6">{nameInput}</div>}
        </fieldset>
      ) : (
        nameInput
      )}

      <div>
        <Label htmlFor="portal-refills-pharmacy">{PHARMACY_LABEL}</Label>
        <Input
          id="portal-refills-pharmacy"
          data-testid="portal-refills-pharmacy"
          value={pharmacy}
          onChange={(event) => setPharmacy(event.target.value)}
          maxLength={PHARMACY_TEXT_MAX}
          disabled={submitting}
          className="mt-1"
        />
      </div>

      <p
        role="note"
        data-testid="portal-refills-crisis-line"
        className="rounded-md border border-neutral-200 bg-neutral-50 p-3 text-sm text-neutral-700"
      >
        {CRISIS_LINE}
      </p>

      <div>
        <Label htmlFor="portal-refills-note">{NOTE_LABEL}</Label>
        <Textarea
          id="portal-refills-note"
          data-testid="portal-refills-note"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          maxLength={PATIENT_NOTE_MAX}
          disabled={submitting}
          rows={3}
          className="mt-1"
        />
      </div>

      {error && (
        <p data-testid="portal-refills-error" role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}

      <div>
        <Button type="submit" data-testid="portal-refills-submit" disabled={!canSubmit}>
          {submitting ? SUBMITTING : SUBMIT}
        </Button>
      </div>
    </form>
  )
}
