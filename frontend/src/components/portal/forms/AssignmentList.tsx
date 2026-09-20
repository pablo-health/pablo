// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The forms this patient has been asked for.
 *
 * One row each: what the practice calls it, where it has got to, and the way
 * back into it. "Continue" is the only action — resuming is the server's
 * answer to what is still outstanding, not this screen's guess, so the row
 * does not try to say which question comes next.
 *
 * What a row says about progress comes from `progress`, which the server
 * computed from the rows just now. Nothing here counts anything.
 */

import { Button } from "@/components/ui/button"
import type { IntakeAssignment } from "@/lib/api/patientIntake"
import {
  LIST_CONTINUE,
  LIST_EMPTY,
  LIST_HEADING,
  LIST_PROGRESS_DONE,
  LIST_SENT,
  LIST_START,
  LIST_WITHDRAWN,
  questionsLeft,
} from "./formsCopy"

/** The statuses a patient can still write to. Mirrors `WRITABLE_STATUSES`. */
const OPEN_STATUSES = new Set(["assigned", "in_progress", "needs_correction"])

interface AssignmentListProps {
  assignments: IntakeAssignment[]
  onOpen: (assignmentId: string) => void
}

export function AssignmentList({ assignments, onOpen }: AssignmentListProps) {
  return (
    <section data-testid="forms-list" aria-labelledby="forms-list-heading">
      <h2 id="forms-list-heading" className="text-lg font-semibold text-neutral-900">
        {LIST_HEADING}
      </h2>

      {assignments.length === 0 ? (
        <p data-testid="forms-list-empty" className="mt-4 text-sm text-neutral-600">
          {LIST_EMPTY}
        </p>
      ) : (
        <ul className="mt-4 flex flex-col gap-3">
          {assignments.map((assignment) => (
            <AssignmentRow key={assignment.id} assignment={assignment} onOpen={onOpen} />
          ))}
        </ul>
      )}
    </section>
  )
}

function AssignmentRow({
  assignment,
  onOpen,
}: {
  assignment: IntakeAssignment
  onOpen: (assignmentId: string) => void
}) {
  const open = OPEN_STATUSES.has(assignment.status)
  const started = assignment.status !== "assigned"

  return (
    <li
      data-testid={`forms-list-row-${assignment.id}`}
      className="rounded-md border border-neutral-200 p-4"
    >
      <p className="text-sm font-medium text-neutral-900">
        {assignment.packet_name} <span className="sr-only">version {assignment.version}</span>
      </p>
      <p data-testid="forms-list-state" className="mt-1 text-xs text-neutral-500">
        {stateLine(assignment)}
      </p>
      {open && (
        <Button
          data-testid="forms-list-open"
          className="mt-3 w-full"
          size="lg"
          onClick={() => onOpen(assignment.id)}
        >
          {started ? LIST_CONTINUE : LIST_START}
        </Button>
      )}
    </li>
  )
}

/**
 * One line about where this form has got to.
 *
 * "Ready to send" is the server's `complete`, never a count this screen did.
 * An outstanding count is a fact about the rows, so it is safe to show; what
 * it means for the patient — whether they can hand the form in — is only
 * ever the server's `complete`.
 */
function stateLine(assignment: IntakeAssignment): string {
  if (assignment.status === "withdrawn") return LIST_WITHDRAWN
  if (!OPEN_STATUSES.has(assignment.status)) return LIST_SENT
  if (assignment.progress.complete) return LIST_PROGRESS_DONE
  return questionsLeft(assignment.progress.missing.length)
}
