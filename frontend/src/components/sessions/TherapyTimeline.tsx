// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react"
import { pointInRecording } from "@/lib/visitTimes"
import {
  LABEL_NAME,
  TURN_LABELS,
  moveBoundary,
  nearestTurn,
  relabel,
  runsOf,
  type Labels,
  type Run,
} from "@/lib/therapyTimeline"
import type { RecordingTurn, RunLabel } from "@/types/visitTimes"

const LABEL_COLOR: Record<RunLabel, string> = {
  therapy: "bg-primary-500",
  medication_management: "bg-amber-400",
  screening_risk: "bg-rose-400",
  admin: "bg-neutral-400",
  unattributed: "bg-neutral-100",
}

export interface TherapyTimelineProps {
  turns: RecordingTurn[]
  labels: Labels
  onChange: (labels: Labels) => void
  /** The client-present span: the bar runs from the recording's start to here. */
  endSeconds: number
  startedAt: string | null
  timeZone: string
}

/**
 * The client-present span as a bar of labeled runs. Click a run to relabel
 * it; drag the line between two runs (or use the arrow keys on it) to move
 * it one turn at a time.
 */
export function TherapyTimeline({
  turns,
  labels,
  onChange,
  endSeconds,
  startedAt,
  timeZone,
}: TherapyTimelineProps) {
  const bar = useRef<HTMLDivElement>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const runs = runsOf(turns, labels)
  const at = (seconds: number) => pointInRecording(seconds, startedAt, timeZone)
  const percent = (seconds: number) => `${(seconds / endSeconds) * 100}%`
  const span = (run: Run) => `${at(run.start)} to ${at(run.end)}`

  // A boundary sits at the first turn of every run but the first; it moves
  // between the second turn of the run before and the last turn of the run after.
  const move = (index: number, to: number) => {
    const before = runs[index - 1]
    const after = runs[index]
    const clamped = Math.min(Math.max(to, before.first + 1), after.last)
    if (clamped !== after.first) {
      setSelected(null)
      onChange(moveBoundary(labels, after.first, clamped))
    }
  }

  const onKey = (index: number) => (e: KeyboardEvent) => {
    const step = e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0
    if (step === 0) return
    e.preventDefault()
    move(index, runs[index].first + step)
  }

  const onDrag = (index: number) => (e: PointerEvent) => {
    if (!e.currentTarget.hasPointerCapture(e.pointerId) || !bar.current) return
    const box = bar.current.getBoundingClientRect()
    const seconds = ((e.clientX - box.left) / box.width) * endSeconds
    move(index, nearestTurn(turns, seconds, runs[index - 1].first + 1, runs[index].last))
  }

  const chosen = selected === null ? null : runs[selected]

  return (
    <div data-testid="therapy-timeline" className="space-y-2">
      <div ref={bar} className="relative flex h-8 w-full overflow-hidden rounded border border-neutral-200">
        <div style={{ flexBasis: percent(runs[0]?.start ?? endSeconds) }} />
        {runs.map((run, i) => (
          <button
            key={`${run.first}-${run.label}`}
            type="button"
            data-testid="timeline-run"
            data-label={run.label}
            aria-label={`${LABEL_NAME[run.label]}, ${span(run)}`}
            aria-pressed={selected === i}
            title={`${LABEL_NAME[run.label]}, ${span(run)}`}
            className={`${LABEL_COLOR[run.label]} h-full border-r border-white last:border-r-0 ${
              selected === i ? "ring-2 ring-inset ring-neutral-900" : ""
            }`}
            style={{ flexBasis: percent(run.end - run.start) }}
            onClick={() => setSelected(selected === i ? null : i)}
          />
        ))}
        {runs.slice(1).map((run, j) => (
          <div
            key={`boundary-${run.first}`}
            role="slider"
            tabIndex={0}
            aria-label={`Boundary at ${at(run.start)}`}
            aria-valuemin={runs[j].first + 1}
            aria-valuemax={run.last}
            aria-valuenow={run.first}
            aria-valuetext={at(run.start)}
            className="absolute top-0 h-full w-2 -translate-x-1/2 cursor-ew-resize focus:outline focus:outline-2 focus:outline-neutral-900"
            style={{ left: percent(run.start) }}
            onKeyDown={onKey(j + 1)}
            onPointerDown={(e) => e.currentTarget.setPointerCapture(e.pointerId)}
            onPointerMove={onDrag(j + 1)}
          />
        ))}
      </div>

      {chosen && selected !== null && (
        <div role="group" aria-label={`Label ${span(chosen)}`} className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-neutral-600">{span(chosen)} is</span>
          {TURN_LABELS.map((label) => (
            <button
              key={label}
              type="button"
              aria-pressed={chosen.label === label}
              className="rounded border border-neutral-300 px-2 py-0.5 hover:bg-neutral-50 aria-pressed:bg-neutral-900 aria-pressed:text-white"
              onClick={() => {
                setSelected(null)
                onChange(relabel(labels, chosen, label))
              }}
            >
              {LABEL_NAME[label]}
            </button>
          ))}
        </div>
      )}

      <ul className="flex flex-wrap gap-3 text-xs text-neutral-600">
        {TURN_LABELS.map((label) => (
          <li key={label} className="flex items-center gap-1">
            <span className={`inline-block h-2 w-3 rounded-sm ${LABEL_COLOR[label]}`} />
            {LABEL_NAME[label]}
          </li>
        ))}
      </ul>
    </div>
  )
}
