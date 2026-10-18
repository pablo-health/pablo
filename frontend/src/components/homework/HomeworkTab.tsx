// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * HomeworkTab
 *
 * What a client was asked to do between visits, and whether they did it.
 *
 * Suggestions are not here. Homework read from a note's plan is suggested
 * with the note's other chart updates and decided with them at signing (or
 * later, above the tabs, while it waits); accepting one is what puts it on
 * this list.
 *
 * Open assignments come first, each done or not yet as the client marked it
 * in the portal, with the note they left. A withdrawn assignment is gone
 * from the client's list and from this one.
 *
 * A clinician can also write an assignment by hand: that is assigning, so
 * it goes straight to the list.
 */

"use client"

import { useState } from "react"
import { ClipboardCheck } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { useToast } from "@/components/ui/Toast"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import { MEDIUM_DATE, formatDate } from "@/lib/dates"
import {
  useAddHomework,
  useEditHomework,
  usePatientHomework,
  useWithdrawHomework,
} from "@/hooks/useHomework"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { usePortalSettings } from "@/hooks/usePortalSettings"
import { HOMEWORK_TEXT_MAX, type HomeworkAssignment } from "@/types/homework"
import {
  ADD_LABEL,
  ADD_PLACEHOLDER,
  CANCEL,
  EDIT,
  EMPTY,
  LOAD_FAILED,
  NOT_DONE,
  REMOVE,
  SAVE,
  SAVE_FAILED,
  TEXT_REQUIRED,
  doneOn,
  portalLine,
  progress,
} from "./homeworkCopy"

/** Open first, then done; newest first within each. */
function byOpenFirst(a: HomeworkAssignment, b: HomeworkAssignment): number {
  if (a.status !== b.status) return a.status === "assigned" ? -1 : 1
  return b.assigned_at.localeCompare(a.assigned_at)
}

interface TextFormProps {
  id: string
  label: string
  submitLabel: string
  initial?: string
  placeholder?: string
  pending: boolean
  onSave: (text: string) => Promise<boolean>
  onCancel?: () => void
}

/** One line of homework text. An empty one is flagged on submit, never sent. */
function TextForm({
  id,
  label,
  submitLabel,
  initial = "",
  placeholder,
  pending,
  onSave,
  onCancel,
}: TextFormProps) {
  const [text, setText] = useState(initial)
  const [missing, setMissing] = useState(false)
  return (
    <form
      className="flex flex-col gap-2 sm:flex-row sm:items-start"
      onSubmit={async (event) => {
        event.preventDefault()
        if (!text.trim()) {
          setMissing(true)
          return
        }
        if ((await onSave(text.trim())) && !onCancel) setText("")
      }}
    >
      <div className="flex-1">
        <label htmlFor={id} className="sr-only">
          {label}
        </label>
        <Input
          id={id}
          placeholder={placeholder}
          aria-invalid={missing || undefined}
          value={text}
          maxLength={HOMEWORK_TEXT_MAX}
          onChange={(event) => {
            setText(event.target.value)
            setMissing(false)
          }}
        />
        {missing && <p className="mt-1 text-xs text-red-600">{TEXT_REQUIRED}</p>}
      </div>
      <span className="flex gap-1">
        <Button type="submit" size="sm" disabled={pending}>
          {submitLabel}
        </Button>
        {onCancel && (
          <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
            {CANCEL}
          </Button>
        )}
      </span>
    </form>
  )
}

function Assignment({
  patientId,
  assignment,
  readOnly,
}: {
  patientId: string
  assignment: HomeworkAssignment
  readOnly: boolean
}) {
  const withdraw = useWithdrawHomework()
  const edit = useEditHomework()
  const { showToast } = useToast()
  const [editing, setEditing] = useState(false)
  const done = assignment.status === "done"

  async function reword(text: string): Promise<boolean> {
    try {
      await edit.mutateAsync({ patientId, assignmentId: assignment.id, text })
      setEditing(false)
      return true
    } catch {
      showToast(SAVE_FAILED, "error")
      return false
    }
  }

  async function remove() {
    try {
      await withdraw.mutateAsync({ patientId, assignmentId: assignment.id })
    } catch {
      showToast(SAVE_FAILED, "error")
    }
  }

  return (
    <li
      data-testid="homework-assignment"
      data-status={assignment.status}
      className="flex flex-col gap-1 rounded-lg border border-neutral-100 px-3 py-2.5"
    >
      {editing ? (
        <TextForm
          id={`homework-reword-${assignment.id}`}
          label={EDIT}
          submitLabel={SAVE}
          initial={assignment.text}
          pending={edit.isPending}
          onSave={reword}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <span className="flex flex-wrap items-center justify-between gap-2">
          <span className="min-w-0 text-sm font-medium text-neutral-900">{assignment.text}</span>
          <span className="flex shrink-0 items-center gap-1">
            <span
              data-testid="homework-assignment-status"
              className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${
                done ? "bg-green-100 text-green-800" : "bg-neutral-100 text-neutral-700"
              }`}
            >
              {done && assignment.completed_at
                ? doneOn(formatDate(assignment.completed_at, MEDIUM_DATE))
                : NOT_DONE}
            </span>
            {!readOnly && !done && (
              <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
                {EDIT}
              </Button>
            )}
            {!readOnly && (
              <Button size="sm" variant="ghost" onClick={remove} disabled={withdraw.isPending}>
                {REMOVE}
              </Button>
            )}
          </span>
        </span>
      )}
      {assignment.client_note && (
        <span data-testid="homework-client-note" className="text-sm text-neutral-700">
          “{assignment.client_note}”
        </span>
      )}
    </li>
  )
}

export function HomeworkTab({ patientId }: { patientId: string }) {
  const { data, isPending, error } = usePatientHomework(patientId)
  const add = useAddHomework()
  const { data: portal } = usePortalSettings()
  const people = usePeopleTerm()
  const { showToast } = useToast()
  const { readOnly } = useReadOnlyMode()

  if (isPending) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    )
  }

  if (error || !data) {
    return <p className="text-sm text-red-500">{LOAD_FAILED}</p>
  }

  const live = data.assignments
    .filter((a) => a.status === "assigned" || a.status === "done")
    .sort(byOpenFirst)
  const doneCount = live.filter((a) => a.status === "done").length
  const portalOn = portal?.enabled === true && portal.modules?.homework === true

  async function addOne(text: string): Promise<boolean> {
    try {
      await add.mutateAsync({ patientId, text })
      return true
    } catch {
      showToast(SAVE_FAILED, "error")
      return false
    }
  }

  return (
    <div className="space-y-4">
      {live.length > 0 && (
        <p data-testid="homework-progress" className="text-xs text-neutral-600">
          {progress(doneCount, live.length)}
        </p>
      )}
      {live.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-6 text-center">
          <ClipboardCheck className="h-8 w-8 text-neutral-300" />
          <p className="text-sm text-neutral-600">{EMPTY}</p>
        </div>
      ) : (
        <ul className="space-y-2" aria-label="Homework">
          {live.map((a) => (
            <Assignment key={a.id} patientId={patientId} assignment={a} readOnly={readOnly} />
          ))}
        </ul>
      )}
      {portalOn && <p className="text-xs text-neutral-500">{portalLine(people.Many)}</p>}
      {!readOnly && (
        <TextForm
          id="homework-add"
          label={ADD_LABEL}
          submitLabel={ADD_LABEL}
          placeholder={ADD_PLACEHOLDER}
          pending={add.isPending}
          onSave={addOne}
        />
      )}
    </div>
  )
}
