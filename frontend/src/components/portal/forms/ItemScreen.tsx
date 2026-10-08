// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One question, and the two ways off it.
 *
 * Back goes to the previous question and saves nothing: somebody stepping
 * back to re-read what they typed has not answered anything new, and a save
 * on the way out would write a half-typed answer the route would refuse.
 *
 * Continue saves and then advances — one PUT per press, never on a timer.
 * No autosave is deliberate: fewer writes of a person's clinical answers,
 * and a state the patient can see. What the save route says about the
 * answer is shown here, beside the question, because it names the question
 * and what to do about it and never repeats the answer back.
 */

import { Button } from "@/components/ui/button"
import type { IntakeArtifact, IntakeAssignmentItem, IntakeForm } from "@/lib/api/patientIntake"
import { CrisisFooter } from "./CrisisFooter"
import {
  BACK,
  CONTINUE,
  SAVING,
  partPosition,
  positionInPart,
  questionPosition,
} from "./formsCopy"
import type { PartPlace } from "./parts"
import { Instructions } from "./renderers/DisplayItem"
import { rendererFor } from "./renderers/registry"
import type { AnswerValue } from "./renderers/types"

interface ItemScreenProps {
  item: IntakeAssignmentItem
  value: AnswerValue | null
  onChange: (value: AnswerValue) => void
  form: IntakeForm | null
  /** Handed to the renderer, for the types that write for themselves. */
  assignmentId: string
  sessionToken: string
  /** What has already arrived for this question. Empty for most types. */
  artifacts: IntakeArtifact[]
  onWrote: () => void
  onSessionLost: () => void
  /** Absent on the first question, so there is nothing to go back to. */
  onBack: (() => void) | null
  onContinue: () => void
  saving: boolean
  /** The server's own sentence about the last attempt to save this answer. */
  error: string | null
  /** Which part of the form this question is in, and where in it. */
  place: PartPlace | null
  /** Instructions that lead into this question, shown above it. */
  notes?: IntakeAssignmentItem[]
}

export function ItemScreen({
  item,
  value,
  onChange,
  form,
  assignmentId,
  sessionToken,
  artifacts,
  onWrote,
  onSessionLost,
  onBack,
  onContinue,
  saving,
  error,
  place,
  notes = [],
}: ItemScreenProps) {
  const renderer = rendererFor(item.item_type)

  return (
    <div data-testid="forms-item-screen" className="flex flex-col">
      {place && <PartHeader place={place} />}

      {notes.map((note) => (
        <div key={note.id} className="mt-3">
          <Instructions item={note} />
        </div>
      ))}

      <div className="mt-3">
        <renderer.Component
          item={item}
          value={value}
          onChange={onChange}
          form={form}
          assignmentId={assignmentId}
          sessionToken={sessionToken}
          artifacts={artifacts}
          onWrote={onWrote}
          onSessionLost={onSessionLost}
        />
      </div>

      {renderer.crisisFooter && <CrisisFooter />}

      {error && (
        <p data-testid="forms-item-error" className="mt-4 text-sm text-red-600">
          {error}
        </p>
      )}

      <div className="mt-6 flex gap-2">
        {onBack && (
          <Button variant="outline" data-testid="forms-back" disabled={saving} onClick={onBack}>
            {BACK}
          </Button>
        )}
        <Button
          className="flex-1"
          size="lg"
          data-testid="forms-continue"
          disabled={saving}
          onClick={onContinue}
        >
          {saving ? SAVING : CONTINUE}
        </Button>
      </div>
    </div>
  )
}

/**
 * The part this question belongs to, and how far into it.
 *
 * Two quiet lines rather than a bar: the parts are counted only when there
 * is more than one, and the count inside a part sits beside its name. A
 * part with no name — the start of a form with no sections — keeps the
 * plain "Question 2 of 3".
 */
function PartHeader({ place }: { place: PartPlace }) {
  const { title, question } = place
  return (
    <div data-testid="forms-part-header" className="flex flex-col gap-0.5">
      {place.parts > 1 && (
        <p data-testid="forms-part-count" className="text-xs text-neutral-500">
          {partPosition(place.part, place.parts)}
        </p>
      )}
      {(title !== null || question !== null) && (
        <p className="text-sm font-medium text-neutral-700">
          {title !== null && <span data-testid="forms-part-title">{title}</span>}
          {title !== null && question !== null && (
            <span aria-hidden="true" className="text-neutral-400">
              {" · "}
            </span>
          )}
          {question !== null && (
            <span data-testid="forms-progress" className="font-normal text-neutral-500">
              {title !== null
                ? positionInPart(question.index, question.total)
                : questionPosition(question.index, question.total)}
            </span>
          )}
        </p>
      )}
    </div>
  )
}
