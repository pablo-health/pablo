// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A photograph of an insurance card, and what is written on it.
 *
 * One slot per side the question asked for, each backed by the shared
 * upload slot. On a phone the picker opens the camera, because a card is
 * something in the client's hand rather than a file on a disk.
 *
 * **It writes for itself, like a consent document.** The question's answer
 * names the documents that arrived, so only the route that records an
 * arrival may write it. Continue does not save anything here — the walk
 * skips the save for a type whose renderer writes its own — and what is on
 * screen after an upload is the server's answer, re-read.
 *
 * **The typed details are not the answer.** When a practice asks for them
 * as well, they go onto the client's coverage record through a route of
 * their own, which is where the chart, a claim and an eligibility check all
 * already read a plan from. The question is still answered by photographing
 * the card: somebody who cannot read a worn card has answered it.
 */

import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  PatientIntakeError,
  saveIntakeCoverage,
  type IntakeCoverageFields,
} from "@/lib/api/patientIntake"
import {
  CARD_BACK,
  CARD_FRONT,
  COVERAGE_FAILED,
  COVERAGE_GROUP_LABEL,
  COVERAGE_HEADING,
  COVERAGE_MEMBER_LABEL,
  COVERAGE_NOTE,
  COVERAGE_PAYER_LABEL,
  COVERAGE_SAVE,
  COVERAGE_SAVED,
  COVERAGE_SAVING,
  filesSent,
} from "../formsCopy"
import { QuestionFrame, labelOf } from "./QuestionFrame"
import { acceptOf, UploadSlot } from "./UploadSlot"
import type { AnswerValue, ItemRenderer, ItemRendererProps } from "./types"

/** Which sides this question asked for. `"front"` narrows it to one. */
function sidesOf(config: Record<string, unknown>): string[] {
  return config.sides === "front" ? ["front"] : ["front", "back"]
}

function collectsFields(config: Record<string, unknown>): boolean {
  return config.collect_fields === true
}

function sideLabel(side: string): string {
  return side === "back" ? CARD_BACK : CARD_FRONT
}

function InsuranceCardItem({
  item,
  artifacts,
  assignmentId,
  sessionToken,
  onWrote,
  onSessionLost,
}: ItemRendererProps) {
  const sides = sidesOf(item.config)
  const accept = acceptOf(item.config)

  return (
    <QuestionFrame item={item}>
      {() => (
        <div className="space-y-5">
          <div data-testid="forms-insurance-card" className="space-y-4">
            {sides.map((side) => (
              <UploadSlot
                key={side}
                assignmentId={assignmentId}
                sessionToken={sessionToken}
                itemId={item.id}
                label={sideLabel(side)}
                side={side}
                artifact={artifacts.find((row) => row.side === side) ?? null}
                accept={accept}
                camera
                onWrote={onWrote}
                onSessionLost={onSessionLost}
              />
            ))}
          </div>

          {collectsFields(item.config) && (
            <CoverageFields
              assignmentId={assignmentId}
              sessionToken={sessionToken}
              itemId={item.id}
              onSessionLost={onSessionLost}
            />
          )}
        </div>
      )}
    </QuestionFrame>
  )
}

/**
 * The plan details printed on the card.
 *
 * Saved by a button of their own rather than on Continue, because they do
 * not go where the rest of the form's answers go. The screen says so by
 * having its own action and its own confirmation, and says nothing about
 * what the practice will do with them — at this point nobody has checked
 * anything with a payer.
 */
function CoverageFields({
  assignmentId,
  sessionToken,
  itemId,
  onSessionLost,
}: {
  assignmentId: string
  sessionToken: string
  itemId: string
  onSessionLost: () => void
}) {
  const [payerName, setPayerName] = useState("")
  const [memberId, setMemberId] = useState("")
  const [groupNumber, setGroupNumber] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const save = useMutation({
    mutationFn: (fields: IntakeCoverageFields) =>
      saveIntakeCoverage(sessionToken, assignmentId, itemId, fields),
    onSuccess: () => {
      setSaved(true)
      setError(null)
    },
    onError: (raised: unknown) => {
      if (raised instanceof PatientIntakeError && raised.kind === "expired") {
        onSessionLost()
        return
      }
      setError(
        raised instanceof PatientIntakeError
          ? (raised.serverMessage ?? COVERAGE_FAILED)
          : COVERAGE_FAILED,
      )
    },
  })

  const canSave = payerName.trim() !== "" && memberId.trim() !== ""

  return (
    <section data-testid="forms-coverage-fields" className="space-y-3 border-t border-neutral-200 pt-5">
      <div>
        <h3 className="text-sm font-semibold text-neutral-900">{COVERAGE_HEADING}</h3>
        <p className="mt-1 text-sm text-neutral-600">{COVERAGE_NOTE}</p>
      </div>

      <Field
        id={`forms-coverage-payer-${itemId}`}
        testId="forms-coverage-payer"
        label={COVERAGE_PAYER_LABEL}
        value={payerName}
        onChange={(next) => {
          setPayerName(next)
          setSaved(false)
        }}
      />
      <Field
        id={`forms-coverage-member-${itemId}`}
        testId="forms-coverage-member"
        label={COVERAGE_MEMBER_LABEL}
        value={memberId}
        onChange={(next) => {
          setMemberId(next)
          setSaved(false)
        }}
      />
      <Field
        id={`forms-coverage-group-${itemId}`}
        testId="forms-coverage-group"
        label={COVERAGE_GROUP_LABEL}
        value={groupNumber}
        onChange={(next) => {
          setGroupNumber(next)
          setSaved(false)
        }}
      />

      <div className="flex items-center gap-3">
        <Button
          type="button"
          variant="outline"
          data-testid="forms-coverage-save"
          disabled={!canSave || save.isPending}
          onClick={() =>
            save.mutate({
              payer_name: payerName.trim(),
              member_id: memberId.trim(),
              group_number: groupNumber.trim() === "" ? null : groupNumber.trim(),
            })
          }
        >
          {save.isPending ? COVERAGE_SAVING : COVERAGE_SAVE}
        </Button>
        {saved && !save.isPending && (
          <span data-testid="forms-coverage-saved" className="text-sm text-neutral-600">
            {COVERAGE_SAVED}
          </span>
        )}
      </div>

      {error !== null && (
        <p data-testid="forms-coverage-error" className="text-sm text-red-600">
          {error}
        </p>
      )}
    </section>
  )
}

function Field({
  id,
  testId,
  label,
  value,
  onChange,
}: {
  id: string
  testId: string
  label: string
  value: string
  onChange: (next: string) => void
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm text-neutral-800">
        {label}
      </label>
      <Input
        id={id}
        data-testid={testId}
        value={value}
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  )
}

/**
 * The review row for a card.
 *
 * Counts what arrived rather than naming files: a filename is the client's
 * own and the review screen repeats what they said, not what their phone
 * called it.
 */
function summaryOf(value: AnswerValue | null): string | null {
  const documents = value?.documents
  if (!Array.isArray(documents) || documents.length === 0) return null
  return filesSent(documents.length)
}

export const insuranceCardRenderer: ItemRenderer = {
  Component: InsuranceCardItem,
  // The walk's save route refuses this type outright: its answer names the
  // documents that arrived, and only the route that records an arrival may
  // write one.
  answerable: false,
  // But it is a question, not a heading — so it is counted and reviewed.
  writesItself: true,
  label: labelOf,
  summary: summaryOf,
}
