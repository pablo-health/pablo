// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { useConfirmPsychotherapyWindow } from "@/hooks/useVisitTimes"
import { addOnBand, wholeMinutes } from "@/lib/visitTimes"
import {
  LEVEL_NAME,
  isThin,
  remainderMinutes,
  therapyMinutes,
  type Labels,
  type MdmLevel,
} from "@/lib/therapyTimeline"
import type {
  ConfirmPsychotherapyWindowRequest,
  PsychotherapyWindow as Window,
  TurnLabel,
} from "@/types/visitTimes"
import { TherapyTimeline } from "./TherapyTimeline"

export interface PsychotherapyWindowProps {
  sessionId: string
  window: Window
  startedAt: string | null
  timeZone: string
  readonly?: boolean
  /** The MDM level chosen for the visit, when there is one, to flag a thin medical visit. */
  mdmLevel?: MdmLevel | null
}

/**
 * The therapy minutes of a visit, confirmed by the clinician.
 *
 * Every turn while the client was present carries a proposed label (see
 * app.notes.visit_times); the minutes are the therapy turns added up. The
 * clinician relabels runs or moves a boundary on the timeline, keeps the
 * minutes they dictated, or types the minutes.
 */
export function PsychotherapyWindow({
  sessionId,
  window,
  startedAt,
  timeZone,
  readonly,
  mdmLevel,
}: PsychotherapyWindowProps) {
  const confirm = useConfirmPsychotherapyWindow(sessionId)
  const people = usePeopleTerm()
  const confirmed = window.confirmed_minutes !== null
  const [editing, setEditing] = useState(!confirmed)
  const [labels, setLabels] = useState<Labels>(() => window.turns.map((t) => t.label))
  const [typing, setTyping] = useState(window.turns.length === 0)
  const [typed, setTyped] = useState("")
  const end = window.end_seconds ?? 0
  const maxMinutes = wholeMinutes(end)
  const dictatedMinutes = window.dictated?.minutes ?? null

  const labeled = therapyMinutes(window.turns, labels)
  const typedMinutes = typed.trim() === "" ? null : Number(typed)
  const minutes = typing ? typedMinutes : labeled
  const tooLong = typing && typedMinutes !== null && typedMinutes > maxMinutes
  const invalid = minutes === null || Number.isNaN(minutes) || minutes < 0 || tooLong
  const remainder = minutes === null || Number.isNaN(minutes) ? null : remainderMinutes(end, minutes)

  const labeledTurns = () =>
    window.turns.flatMap((t, i) => {
      const label: TurnLabel | null = labels[i]
      return label ? [{ seconds: t.seconds, label }] : []
    })

  const save = (body: ConfirmPsychotherapyWindowRequest) =>
    confirm.mutate(body, { onSuccess: () => setEditing(false) })

  // Settling a disagreement re-sends what was confirmed, with the clinician's pick.
  const resolve = (resolution: NonNullable<ConfirmPsychotherapyWindowRequest["resolution"]>) => {
    const base = { time_zone: timeZone, resolution }
    if (window.labels_confirmed) {
      const kept = window.turns.flatMap((t) => (t.label ? [{ seconds: t.seconds, label: t.label }] : []))
      save({ ...base, labels: kept })
    } else if (window.confirmed_start_seconds !== null) {
      save({ ...base, start_seconds: window.confirmed_start_seconds })
    } else {
      save({ ...base, minutes: window.confirmed_minutes ?? 0 })
    }
  }

  return (
    <div data-testid="psychotherapy-window" className="space-y-2">
      <h4 className="text-sm font-semibold text-neutral-900">Psychotherapy time</h4>

      {confirmed && !editing && (
        <p className="text-sm text-neutral-700">
          <span data-testid="psychotherapy-confirmed">{window.window_text}</span>
          {" · "}
          <span data-testid="add-on-band">{addOnBand(window.confirmed_minutes ?? 0)}</span>
          {!readonly && (
            <>
              {" · "}
              <button
                type="button"
                className="font-medium text-primary-700 hover:underline"
                onClick={() => setEditing(true)}
              >
                Change
              </button>
            </>
          )}
        </p>
      )}

      {window.disagrees && !editing && (
        <div role="alert" className="rounded border border-amber-300 bg-amber-50 p-3 text-sm">
          <p>
            You said “{window.dictated_time}”. The transcript shows {window.window_text}.
          </p>
          {!readonly && (
            <div className="mt-2 flex gap-2">
              <Button size="sm" onClick={() => resolve("use_confirmed")} disabled={confirm.isPending}>
                Use {window.confirmed_minutes} minutes
              </Button>
              <Button size="sm" variant="outline" onClick={() => resolve("keep_dictated")} disabled={confirm.isPending}>
                Keep what you said
              </Button>
            </div>
          )}
        </div>
      )}

      {editing && !readonly && (
        <div className="space-y-2 text-sm">
          {window.turns.length > 0 && !typing && (
            <TherapyTimeline
              turns={window.turns}
              labels={labels}
              onChange={setLabels}
              endSeconds={end}
              startedAt={startedAt}
              timeZone={timeZone}
            />
          )}

          {typing && (
            <label className="flex items-center gap-2">
              Therapy minutes
              <input
                aria-label="Psychotherapy minutes"
                type="number"
                min={0}
                className="w-20 rounded border border-neutral-300 px-2 py-1"
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
              />
            </label>
          )}

          {tooLong ? (
            <p role="alert" className="text-red-600">
              Can&apos;t be more than the {maxMinutes} minutes the {people.one} was present.
            </p>
          ) : (
            minutes !== null &&
            !Number.isNaN(minutes) && (
              <p data-testid="psychotherapy-preview" className="text-neutral-700">
                {minutes} therapy minutes of {maxMinutes} · {addOnBand(minutes)}
              </p>
            )
          )}
          {remainder !== null && !tooLong && (
            <p data-testid="em-remainder" className="text-neutral-600">
              Medical visit: {remainder} min
              {isThin(remainder, mdmLevel) && mdmLevel && (
                <span data-testid="em-remainder-flag" className="text-amber-700">
                  {" "}
                  · short for a {LEVEL_NAME[mdmLevel]} visit
                </span>
              )}
            </p>
          )}

          {dictatedMinutes !== null && (
            <p className="text-neutral-700">You said {dictatedMinutes} minutes.</p>
          )}

          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              onClick={() =>
                save(
                  typing
                    ? { minutes: typedMinutes ?? 0, time_zone: timeZone }
                    : { labels: labeledTurns(), time_zone: timeZone },
                )
              }
              disabled={invalid || confirm.isPending}
            >
              Confirm
            </Button>
            {dictatedMinutes !== null && dictatedMinutes <= maxMinutes && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => save({ minutes: dictatedMinutes, time_zone: timeZone })}
                disabled={confirm.isPending}
              >
                Use my minutes
              </Button>
            )}
            {window.turns.length > 0 && (
              <Button size="sm" variant="outline" onClick={() => setTyping(!typing)}>
                {typing ? "Use the timeline" : "Type the minutes"}
              </Button>
            )}
            {confirmed && (
              <Button size="sm" variant="outline" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            )}
          </div>
          {confirm.isError && (
            <p role="alert" className="text-red-600">
              That wasn&apos;t saved. Try again.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
