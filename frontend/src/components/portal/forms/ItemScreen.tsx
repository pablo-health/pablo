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
import { BACK, CONTINUE, SAVING, questionPosition } from "./formsCopy"
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
  /** Where this question sits among the ones that collect an answer. */
  position: { index: number; total: number } | null
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
  position,
}: ItemScreenProps) {
  const renderer = rendererFor(item.item_type)

  return (
    <div data-testid="forms-item-screen" className="flex flex-col">
      {position && (
        <p data-testid="forms-progress" className="text-xs font-medium text-neutral-500">
          {questionPosition(position.index, position.total)}
        </p>
      )}

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
