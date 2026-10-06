// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { useConfirmPsychotherapyWindow } from "@/hooks/useVisitTimes"
import {
  addOnBand,
  minutesBetween,
  placeStatedTime,
  pointInRecording,
  wholeMinutes,
} from "@/lib/visitTimes"
import type {
  ConfirmPsychotherapyWindowRequest,
  PsychotherapyWindow as Window,
  StartSource,
} from "@/types/visitTimes"

const SOURCE_LABEL: Record<StartSource, string> = {
  spoken_cue: "you said so here",
  marked: "where the draft puts it",
  attributed: "where the interventions begin",
}

const SNIPPET_CHARS = 70

export interface PsychotherapyWindowProps {
  sessionId: string
  window: Window
  startedAt: string | null
  timeZone: string
  readonly?: boolean
}

interface Option {
  key: string
  seconds: number
  label: string
}

/**
 * Where the therapy portion started, confirmed by the clinician, and the
 * minutes that follow from it: from that start to when the client left.
 *
 * The draft proposes starts (see app.notes.visit_times); the clinician picks
 * one, picks another turn, or types the minutes. A start time the clinician
 * said aloud is offered first.
 */
export function PsychotherapyWindow({
  sessionId,
  window,
  startedAt,
  timeZone,
  readonly,
}: PsychotherapyWindowProps) {
  const confirm = useConfirmPsychotherapyWindow(sessionId)
  const people = usePeopleTerm()
  const confirmed = window.confirmed_minutes !== null
  const [editing, setEditing] = useState(!confirmed)
  const end = window.end_seconds ?? 0
  const maxMinutes = wholeMinutes(end)

  const turnAt = (seconds: number) => window.turns.find((t) => t.seconds === seconds)
  const describe = (seconds: number, note: string) => {
    const turn = turnAt(seconds)
    const said = turn ? ` ${turn.speaker}: “${turn.text.slice(0, SNIPPET_CHARS)}”` : ""
    return `${pointInRecording(seconds, startedAt, timeZone)} (${note})${said}`
  }

  const options: Option[] = []
  const stated = window.stated_clock_time
    ? placeStatedTime(window.stated_clock_time, startedAt, timeZone, end)
    : null
  if (stated !== null) {
    options.push({ key: "stated", seconds: stated, label: describe(stated, `you said “${window.stated_clock_time}”`) })
  }
  window.candidates.forEach((c, i) =>
    options.push({ key: `c${i}`, seconds: c.seconds, label: describe(c.seconds, SOURCE_LABEL[c.source]) }),
  )

  const [choice, setChoice] = useState(options[0]?.key ?? "turn")
  const [turnSeconds, setTurnSeconds] = useState<number | null>(window.turns[0]?.seconds ?? null)
  const [typed, setTyped] = useState("")

  const chosenStart =
    choice === "turn" ? turnSeconds : (options.find((o) => o.key === choice)?.seconds ?? null)
  const typedMinutes = typed.trim() === "" ? null : Number(typed)
  const minutes = choice === "typed" ? typedMinutes : chosenStart === null ? null : minutesBetween(chosenStart, end)
  const tooLong = choice === "typed" && typedMinutes !== null && typedMinutes > maxMinutes
  const invalid = minutes === null || Number.isNaN(minutes) || minutes < 0 || tooLong

  const submit = (resolution?: ConfirmPsychotherapyWindowRequest["resolution"]) => {
    const body: ConfirmPsychotherapyWindowRequest =
      resolution && window.confirmed_minutes !== null
        ? window.confirmed_start_seconds !== null
          ? { start_seconds: window.confirmed_start_seconds, time_zone: timeZone, resolution }
          : { minutes: window.confirmed_minutes, time_zone: timeZone, resolution }
        : choice === "typed"
          ? { minutes: typedMinutes ?? 0, time_zone: timeZone }
          : { start_seconds: chosenStart ?? 0, time_zone: timeZone }
    confirm.mutate(body, { onSuccess: () => setEditing(false) })
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
              <Button size="sm" onClick={() => submit("use_confirmed")} disabled={confirm.isPending}>
                Use {window.confirmed_minutes} minutes
              </Button>
              <Button size="sm" variant="outline" onClick={() => submit("keep_dictated")} disabled={confirm.isPending}>
                Keep what you said
              </Button>
            </div>
          )}
        </div>
      )}

      {editing && !readonly && (
        <fieldset className="space-y-2 text-sm">
          <legend className="text-neutral-600">Where did the therapy portion start?</legend>
          {options.map((o) => (
            <label key={o.key} className="flex items-start gap-2">
              <input type="radio" name="therapy-start" checked={choice === o.key} onChange={() => setChoice(o.key)} />
              <span>{o.label}</span>
            </label>
          ))}
          {window.turns.length > 0 && (
            <label className="flex items-start gap-2">
              <input type="radio" name="therapy-start" checked={choice === "turn"} onChange={() => setChoice("turn")} />
              <span className="flex flex-col gap-1">
                Another point in the transcript
                {choice === "turn" && (
                  <select
                    aria-label="Therapy started at"
                    className="rounded border border-neutral-300 px-2 py-1"
                    value={turnSeconds ?? ""}
                    onChange={(e) => setTurnSeconds(Number(e.target.value))}
                  >
                    {window.turns.map((t) => (
                      <option key={t.seconds} value={t.seconds}>
                        {pointInRecording(t.seconds, startedAt, timeZone)} {t.speaker}: {t.text.slice(0, SNIPPET_CHARS)}
                      </option>
                    ))}
                  </select>
                )}
              </span>
            </label>
          )}
          <label className="flex items-start gap-2">
            <input type="radio" name="therapy-start" checked={choice === "typed"} onChange={() => setChoice("typed")} />
            <span className="flex items-center gap-2">
              Type the minutes
              {choice === "typed" && (
                <input
                  aria-label="Psychotherapy minutes"
                  type="number"
                  min={0}
                  className="w-20 rounded border border-neutral-300 px-2 py-1"
                  value={typed}
                  onChange={(e) => setTyped(e.target.value)}
                />
              )}
            </span>
          </label>

          {tooLong ? (
            <p role="alert" className="text-red-600">
              Can&apos;t be more than the {maxMinutes} minutes the {people.one} was present.
            </p>
          ) : (
            minutes !== null &&
            !Number.isNaN(minutes) && (
              <p data-testid="psychotherapy-preview" className="text-neutral-700">
                {minutes} minutes · {addOnBand(minutes)}
              </p>
            )
          )}
          <div className="flex gap-2">
            <Button size="sm" onClick={() => submit()} disabled={invalid || confirm.isPending}>
              Confirm
            </Button>
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
        </fieldset>
      )}
    </div>
  )
}
