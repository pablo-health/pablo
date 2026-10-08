// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Medical decision making, beside the note rather than in it.
 *
 * The clinician picks a level for each element; the draft only supplies a
 * sentence of evidence beside each. The level, the E/M code and the
 * psychotherapy add-on are computed on the server from those choices and the
 * confirmed psychotherapy minutes (app.notes.mdm), never by the model. The
 * note states only the codes, in its visit details.
 *
 * A code the clinician dictated is never changed by itself. When it disagrees
 * with the computed one the panel says so, and offers to put the computed
 * codes in the visit details, which saves them as the clinician's edit.
 *
 * Time is offered as a way to choose the level only when the note has no
 * psychotherapy portion: with an add-on, the level is chosen by MDM alone.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { useApplyMdmCodes, useMdmReview, useSaveMdmChoices } from "@/hooks/useMdmReview"
import { useNoteType } from "@/hooks/useNoteTypes"
import { MDM_CHOICE_INPUTS, NEW_PATIENT_INPUT, levelLabel, offersMdmReview } from "@/lib/mdm"
import { describeServiceCode } from "@/lib/serviceCodes"
import type { MdmChoicesRequest, MdmElement, MdmReview } from "@/types/mdm"
import type { Note } from "@/types/notes"

const ELEMENT_LABEL: Record<MdmElement, string> = {
  problems: "Problems addressed",
  risk: "Risk of management",
  data: "Data reviewed",
}

const SELECT_CLASS =
  "w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"

export interface MdmReviewPanelProps {
  note: Note
  /** False once the note is signed, or while nothing may change it. */
  editable: boolean
  /**
   * Runs before the computed codes go into the note: the page settles any
   * edit it is still saving, so the codes land on the note as it stands.
   */
  beforeApply?: () => Promise<void>
}

export function MdmReviewPanel({ note, editable, beforeApply }: MdmReviewPanelProps) {
  const { data: noteType } = useNoteType(note.note_type, note.note_type_version)
  const offered = !!noteType && offersMdmReview(noteType.inputs)
  const { data: review } = useMdmReview(note.id, note.updated_at, offered)
  if (!noteType || !offered || !review) return null

  const options = Object.fromEntries(noteType.inputs.map((i) => [i.key, i.options]))
  return (
    <MdmReviewBody
      noteId={note.id}
      sessionId={note.session_id}
      review={review}
      options={options}
      editable={editable}
      beforeApply={beforeApply}
    />
  )
}

function choicesOf(review: MdmReview): MdmChoicesRequest {
  const chosen = Object.fromEntries(review.elements.map((e) => [e.element, e.chosen]))
  return {
    problems: chosen.problems ?? null,
    data: chosen.data ?? null,
    risk: chosen.risk ?? null,
    new_patient: review.new_patient ? "new" : "established",
  }
}

const sameChoices = (a: MdmChoicesRequest, b: MdmChoicesRequest) =>
  a.problems === b.problems && a.data === b.data && a.risk === b.risk && a.new_patient === b.new_patient

function MdmReviewBody({
  noteId,
  sessionId,
  review,
  options,
  editable,
  beforeApply,
}: {
  noteId: string
  sessionId: string | null
  review: MdmReview
  options: Record<string, string[]>
  editable: boolean
  beforeApply?: () => Promise<void>
}) {
  const save = useSaveMdmChoices(noteId)
  const applyCodes = useApplyMdmCodes(noteId, sessionId)
  // What the clinician picked, shown until the review comes back saying the
  // same: a review fetched between two quick picks must not undo the first.
  const [picked, setPicked] = useState<MdmChoicesRequest | null>(null)
  const saved = choicesOf(review)
  if (picked && sameChoices(picked, saved)) setPicked(null)
  const choices = picked && !sameChoices(picked, saved) ? picked : saved

  const choose = (change: Partial<MdmChoicesRequest>) => {
    const next = { ...choices, ...change }
    setPicked(next)
    save.mutate(next, { onError: () => setPicked(null) })
  }
  const chooseLevel = (element: MdmElement, level: string | null) =>
    choose(
      element === "problems" ? { problems: level } : element === "data" ? { data: level } : { risk: level },
    )
  const emDescription = review.em_code ? describeServiceCode(review.em_code) : null

  const codes = [review.em_code, review.add_on_known ? review.add_on : null].filter(
    (code): code is string => !!code,
  )
  const disagrees = review.em_disagrees || review.add_on_disagrees
  const applyLabel = disagrees ? "Use the computed codes" : `Add ${codes.join(" and ")} to the note`
  const apply = async () => {
    await beforeApply?.()
    applyCodes.mutate()
  }

  return (
    <section
      className="card space-y-4 p-4"
      aria-labelledby="mdm-heading"
      data-testid="mdm-review"
    >
      <div>
        <h3 id="mdm-heading" className="text-base font-semibold text-neutral-900">
          Medical decision making
        </h3>
        {editable && (
          <p className="text-sm text-neutral-600">Choose a level for each. The code follows.</p>
        )}
      </div>

      <div className="space-y-4">
        {review.elements.map((element) => {
          const id = `mdm-${element.element}`
          const value = choices[element.element] ?? ""
          return (
            <div key={element.element} className="space-y-1.5" data-testid={`mdm-element-${element.element}`}>
              <Label htmlFor={id}>{ELEMENT_LABEL[element.element]}</Label>
              <select
                id={id}
                value={value}
                onChange={(e) => chooseLevel(element.element, e.target.value || null)}
                disabled={!editable}
                className={SELECT_CLASS}
              >
                <option value="">Choose…</option>
                {(options[MDM_CHOICE_INPUTS[element.element]] ?? []).map((option) => (
                  <option key={option} value={option}>
                    {levelLabel(option)}
                  </option>
                ))}
              </select>
              {element.evidence && (
                <p className="text-sm text-neutral-600" data-testid="mdm-evidence">
                  {element.evidence}
                </p>
              )}
              {element.required && (
                <p className="text-xs text-neutral-500" data-testid="mdm-meets">
                  {element.meets
                    ? "Counts toward the level"
                    : `Below ${element.required}; the other two set the level`}
                </p>
              )}
            </div>
          )
        })}

        <div className="space-y-1.5">
          <Label htmlFor="mdm-new-patient">New or established</Label>
          <select
            id="mdm-new-patient"
            value={choices.new_patient ?? ""}
            onChange={(e) =>
              choose({ new_patient: (e.target.value || null) as MdmChoicesRequest["new_patient"] })
            }
            disabled={!editable}
            className={SELECT_CLASS}
          >
            {(options[NEW_PATIENT_INPUT] ?? []).map((option) => (
              <option key={option} value={option}>
                {levelLabel(option)}
              </option>
            ))}
          </select>
        </div>
      </div>

      <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
        <dt className="text-neutral-600">Level</dt>
        <dd className="text-neutral-900" data-testid="mdm-level">
          {review.level ? levelLabel(review.level) : "Choose all three to see the level"}
        </dd>
        <dt className="text-neutral-600">E/M code</dt>
        <dd className="text-neutral-900" data-testid="mdm-em-code">
          {review.em_code ? (
            <>
              <span data-testid="mdm-em-code-value">{review.em_code}</span>
              {emDescription && <span className="text-neutral-600"> · {emDescription}</span>}
            </>
          ) : (
            "Not yet"
          )}
        </dd>
        {review.has_psychotherapy && (
          <>
            <dt className="text-neutral-600">Psychotherapy add-on</dt>
            <dd className="text-neutral-900" data-testid="mdm-add-on">
              {!review.add_on_known
                ? "Confirm the psychotherapy minutes to see it"
                : review.add_on
                  ? `${review.add_on} · ${review.psychotherapy_minutes} confirmed minutes`
                  : `None · ${review.psychotherapy_minutes} confirmed minutes`}
            </dd>
          </>
        )}
        <dt className="text-neutral-600">Level chosen by</dt>
        <dd className="text-neutral-900" data-testid="mdm-billing-methods">
          <span>Medical decision making</span>
          {review.billing_methods.includes("time") && (
            <span data-testid="mdm-billing-time"> or total time on the date of the visit</span>
          )}
        </dd>
      </dl>

      {disagrees && (
        <div role="alert" className="space-y-1 rounded border border-amber-300 bg-amber-50 p-3 text-sm">
          {review.em_disagrees && (
            <p>
              Dictated {review.dictated_em_code}; your choices support {review.em_code}.
            </p>
          )}
          {review.add_on_disagrees && (
            <p>
              {review.has_psychotherapy
                ? `Dictated ${review.dictated_add_on}; ${review.psychotherapy_minutes} confirmed minutes supports ${review.add_on ?? "no add-on"}.`
                : `Dictated ${review.dictated_add_on}; this note has no psychotherapy.`}
            </p>
          )}
        </div>
      )}

      {editable && review.visit_details_with_codes !== null && (
        <div className="flex justify-end">
          <Button type="button" size="sm" onClick={apply} disabled={applyCodes.isPending}>
            {applyLabel}
          </Button>
        </div>
      )}

      {applyCodes.isError && (
        <p role="alert" className="text-sm text-red-600">
          The codes weren&apos;t added. Try again.
        </p>
      )}
      {save.isError && (
        <p role="alert" className="text-sm text-red-600">
          Your choice wasn&apos;t saved. Try again.
        </p>
      )}
    </section>
  )
}
