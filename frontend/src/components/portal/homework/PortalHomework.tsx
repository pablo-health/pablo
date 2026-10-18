// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Homework in the client portal: what the clinician assigned, open items
 * first, each with a way to mark it done.
 *
 * "Mark done" opens a short optional note under the item, so a client can
 * say how it went; the note is sent with the mark and shown to the
 * clinician. The crisis line sits above the note, as it does above every
 * free-text box in the portal, because a note is a place someone might
 * write something urgent and nobody may read it until the next visit.
 *
 * A done item can be marked not done after all, which clears its note.
 *
 * Every change is followed by a refetch, so the list settles on what the
 * store holds rather than on what this page guessed.
 */

"use client"

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  CLIENT_NOTE_MAX,
  listHomework,
  markHomeworkDone,
  markHomeworkNotDone,
  type PatientHomework,
} from "@/lib/api/patientHomework"
import { MEDIUM_DATE, formatDate } from "@/lib/dates"
import { CRISIS_LINE } from "../messaging/ExpectationNotice"
import {
  CANCEL,
  CONFIRM_DONE,
  DONE,
  EMPTY,
  HEADING,
  LOAD_FAILED,
  MARK_DONE,
  NOTE_LABEL,
  NOT_DONE_YET,
  SAVE_FAILED,
  assignedOn,
  doneOn,
} from "./homeworkCopy"

export const homeworkKey = (token: string) => ["patient-homework", token] as const

function shown(iso: string | null): string | null {
  return iso ? formatDate(iso, MEDIUM_DATE, "en-US") : null
}

function HomeworkItem({
  item,
  sessionToken,
}: {
  item: PatientHomework
  sessionToken: string
}) {
  const queryClient = useQueryClient()
  const [noting, setNoting] = useState(false)
  const [note, setNote] = useState("")
  const done = item.status === "done"

  const mark = useMutation({
    mutationFn: (next: { done: boolean; note: string | null }) =>
      next.done
        ? markHomeworkDone(sessionToken, item.id, next.note)
        : markHomeworkNotDone(sessionToken, item.id),
    onSuccess: () => {
      setNoting(false)
      setNote("")
      void queryClient.invalidateQueries({ queryKey: homeworkKey(sessionToken) })
    },
  })

  const date = done ? shown(item.completed_at) : shown(item.assigned_at)

  return (
    <li
      data-testid={`portal-homework-item-${item.id}`}
      data-status={item.status}
      className="flex flex-col gap-2 rounded-md border border-neutral-200 bg-white p-3"
    >
      <span className="flex flex-wrap items-start justify-between gap-2">
        <span className="min-w-0 break-words text-sm font-medium text-neutral-900">
          {item.text}
        </span>
        {done ? (
          <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">
            {date ? doneOn(date) : DONE}
          </span>
        ) : null}
      </span>
      {!done && date && <span className="text-xs text-neutral-500">{assignedOn(date)}</span>}
      {done && item.client_note && (
        <span className="text-sm text-neutral-700">“{item.client_note}”</span>
      )}

      {noting ? (
        <form
          className="flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            mark.mutate({ done: true, note: note.trim() || null })
          }}
        >
          <p className="text-xs text-neutral-600">{CRISIS_LINE}</p>
          <div>
            <Label htmlFor={`portal-homework-note-${item.id}`}>{NOTE_LABEL}</Label>
            <Textarea
              id={`portal-homework-note-${item.id}`}
              data-testid={`portal-homework-note-${item.id}`}
              value={note}
              maxLength={CLIENT_NOTE_MAX}
              onChange={(event) => setNote(event.target.value)}
              className="mt-1"
              rows={2}
            />
          </div>
          <span className="flex gap-2">
            <Button
              type="submit"
              size="sm"
              data-testid={`portal-homework-confirm-${item.id}`}
              disabled={mark.isPending}
            >
              {CONFIRM_DONE}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setNoting(false)}>
              {CANCEL}
            </Button>
          </span>
        </form>
      ) : (
        <span>
          {done ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              data-testid={`portal-homework-undo-${item.id}`}
              onClick={() => mark.mutate({ done: false, note: null })}
              disabled={mark.isPending}
            >
              {NOT_DONE_YET}
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              data-testid={`portal-homework-done-${item.id}`}
              onClick={() => setNoting(true)}
            >
              {MARK_DONE}
            </Button>
          )}
        </span>
      )}
      {mark.isError && (
        <p role="alert" className="text-sm text-red-600">
          {SAVE_FAILED}
        </p>
      )}
    </li>
  )
}

export function PortalHomework({ sessionToken }: { sessionToken: string }) {
  const homework = useQuery({
    queryKey: homeworkKey(sessionToken),
    queryFn: () => listHomework(sessionToken),
  })

  return (
    <div className="flex flex-col gap-3" data-testid="portal-homework">
      <h2 className="text-sm font-semibold text-neutral-900">{HEADING}</h2>
      {homework.isPending ? (
        <p className="text-sm text-neutral-600">Loading…</p>
      ) : homework.isError ? (
        <p data-testid="portal-homework-load-error" className="text-sm text-neutral-600">
          {LOAD_FAILED}
        </p>
      ) : homework.data.data.length === 0 ? (
        <p data-testid="portal-homework-empty" className="text-sm text-neutral-600">
          {EMPTY}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {homework.data.data.map((item) => (
            <HomeworkItem key={item.id} item={item} sessionToken={sessionToken} />
          ))}
        </ul>
      )}
    </div>
  )
}
