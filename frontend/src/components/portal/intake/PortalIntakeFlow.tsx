// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient portal's intake form: confirm who you are, say what brings you
 * in, answer two screeners, send.
 *
 * A stepped wizard rather than one long page. Most people meet this form on
 * a phone, and sixteen screener items plus a free-text box on a single
 * scroll is the shape that gets abandoned halfway.
 *
 * Nothing is saved until the last step. State lives in this component, so
 * closing the tab loses it — acceptable for a form that takes a few minutes,
 * and the alternative is a draft of someone's clinical answers persisted
 * somewhere for a session that may not be theirs.
 *
 * The form's content is the server's: which screeners, their items, their
 * anchors, the reason prompt. This file renders what arrives.
 */

"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  fetchIntakeForm,
  PatientIntakeError,
  submitIntake,
  type IntakeForm,
  type SubmitIntakeRequest,
} from "@/lib/api/patientIntake"
import { CrisisFooter } from "./CrisisFooter"
import { IdentityStep } from "./IdentityStep"
import { IntakeDone } from "./IntakeDone"
import { IntakeExpired, IntakeLoadFailed, IntakeLoading } from "./IntakeNotice"
import { ReasonStep } from "./ReasonStep"
import { ScreenerStep } from "./ScreenerStep"
import {
  BACK,
  CONTINUE,
  SUBMIT,
  SUBMIT_FAILED,
  SUBMIT_RATE_LIMITED,
  SUBMIT_REJECTED,
  SUBMITTING,
} from "./intakeCopy"

/** The screener that carries the crisis line, by the code the server uses. */
const CRISIS_FOOTER_INSTRUMENT = "phq9"

export interface PortalIntakeFlowProps {
  /** The live portal session token. The form never goes looking for one. */
  sessionToken: string
  /** Called once the submission is recorded, for a host that tracks progress. */
  onComplete?: () => void
}

type LoadState =
  | { status: "loading" }
  | { status: "expired" }
  | { status: "failed" }
  | { status: "ready"; form: IntakeForm }

function messageFor(error: unknown): string {
  if (error instanceof PatientIntakeError) {
    if (error.kind === "rate_limited") return SUBMIT_RATE_LIMITED
    if (error.kind === "rejected") return SUBMIT_REJECTED
  }
  return SUBMIT_FAILED
}

export function PortalIntakeFlow({ sessionToken, onComplete }: PortalIntakeFlowProps) {
  const [load, setLoad] = useState<LoadState>({ status: "loading" })
  const [attempt, setAttempt] = useState(0)

  const [stepIndex, setStepIndex] = useState(0)
  const [confirmed, setConfirmed] = useState<boolean | null>(null)
  const [corrections, setCorrections] = useState("")
  const [reason, setReason] = useState("")
  const [answers, setAnswers] = useState<Record<string, Record<string, number>>>({})

  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  // Set synchronously, unlike `submitting`: two clicks landing in one React
  // batch would both see the old state and both POST. The route records a
  // resubmission as a second administration, so a duplicate is a real row.
  const inFlight = useRef(false)

  useEffect(() => {
    let cancelled = false
    fetchIntakeForm(sessionToken)
      .then((form) => {
        if (!cancelled) setLoad({ status: "ready", form })
      })
      .catch((error: unknown) => {
        if (cancelled) return
        const expired = error instanceof PatientIntakeError && error.kind === "expired"
        setLoad({ status: expired ? "expired" : "failed" })
      })
    return () => {
      cancelled = true
    }
  }, [sessionToken, attempt])

  const answerItem = useCallback((code: string, itemKey: string, value: number) => {
    setAnswers((prev) => ({ ...prev, [code]: { ...prev[code], [itemKey]: value } }))
  }, [])

  if (load.status === "loading") return <IntakeLoading />
  if (load.status === "expired") return <IntakeExpired />
  if (load.status === "failed") {
    // The retry puts the skeleton back itself: the effect that refetches is
    // not the place to reset the state it is about to replace.
    return (
      <IntakeLoadFailed
        onRetry={() => {
          setLoad({ status: "loading" })
          setAttempt((n) => n + 1)
        }}
      />
    )
  }
  if (done) return <IntakeDone />

  const { form } = load
  const instruments = form.instruments
  const stepCount = 2 + instruments.length
  const instrument = stepIndex >= 2 ? instruments[stepIndex - 2] : null
  const isLastStep = stepIndex === stepCount - 1

  const instrumentComplete = (code: string, itemCount: number) =>
    Object.keys(answers[code] ?? {}).length === itemCount

  const canContinue = (() => {
    if (stepIndex === 0) return confirmed !== null
    if (stepIndex === 1) return reason.trim().length > 0
    if (!instrument) return false
    return instrumentComplete(instrument.code, Object.keys(instrument.items).length)
  })()

  async function handleSubmit() {
    if (inFlight.current) return
    inFlight.current = true
    setSubmitting(true)
    setSubmitError(null)

    // `name_confirmed` and `dob_confirmed` come from one answer because the
    // screen asks one question: the patient is confirming the record shown,
    // not auditing it field by field. They stay separate on the wire because
    // that is the route's shape and a later screen may split the question.
    const body: SubmitIntakeRequest = {
      name_confirmed: confirmed === true,
      dob_confirmed: confirmed === true,
      corrections: corrections.trim() === "" ? null : corrections.trim(),
      reason_text: reason.trim(),
      phq9: answers.phq9 ?? {},
      gad7: answers.gad7 ?? {},
    }

    try {
      await submitIntake(sessionToken, body)
      setDone(true)
      onComplete?.()
    } catch (error: unknown) {
      if (error instanceof PatientIntakeError && error.kind === "expired") {
        setLoad({ status: "expired" })
        return
      }
      setSubmitError(messageFor(error))
      // Only a failure reopens the button. A recorded submission keeps it
      // shut for good: this screen is replaced by the done screen anyway,
      // and a retry after a 201 would record the answers twice.
      inFlight.current = false
      setSubmitting(false)
    }
  }

  return (
    <div data-testid="intake-flow" className="flex flex-col">
      <p data-testid="intake-progress" className="text-xs font-medium text-neutral-500">
        Step {stepIndex + 1} of {stepCount}
      </p>

      <div className="mt-3">
        {stepIndex === 0 && (
          <IdentityStep
            identity={form.identity}
            confirmed={confirmed}
            onConfirmedChange={setConfirmed}
            corrections={corrections}
            onCorrectionsChange={setCorrections}
          />
        )}
        {stepIndex === 1 && (
          <ReasonStep prompt={form.reason_prompt} value={reason} onChange={setReason} />
        )}
        {instrument && (
          <ScreenerStep
            instrument={instrument}
            answers={answers[instrument.code] ?? {}}
            onAnswer={(itemKey, value) => answerItem(instrument.code, itemKey, value)}
          />
        )}
      </div>

      {instrument?.code === CRISIS_FOOTER_INSTRUMENT && <CrisisFooter />}

      {submitError && (
        <p data-testid="intake-submit-error" className="mt-4 text-sm text-red-600">
          {submitError}
        </p>
      )}

      <div className="mt-6 flex gap-2">
        {stepIndex > 0 && (
          <Button
            variant="outline"
            data-testid="intake-back"
            disabled={submitting}
            onClick={() => setStepIndex((n) => n - 1)}
          >
            {BACK}
          </Button>
        )}
        <Button
          className="flex-1"
          size="lg"
          data-testid={isLastStep ? "intake-submit" : "intake-continue"}
          disabled={!canContinue || submitting}
          onClick={() => {
            if (isLastStep) void handleSubmit()
            else setStepIndex((n) => n + 1)
          }}
        >
          {isLastStep ? (submitting ? SUBMITTING : SUBMIT) : CONTINUE}
        </Button>
      </div>
    </div>
  )
}
