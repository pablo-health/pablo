// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * OutcomeMeasureTrend
 *
 * Presentational trend for a single instrument: a dependency-free inline SVG
 * sparkline of total scores over time, the latest score + severity badge, and
 * a dated list of administrations. Rows that trip the instrument's safety
 * signal (PHQ-9 item 9) carry a persistent amber indicator.
 *
 * Pure render — the parent owns fetching and the delete mutation. Severity and
 * totals come straight from the API; nothing is scored here.
 */

"use client"

import { AlertTriangle, Trash2 } from "lucide-react"
import type { OutcomeMeasure } from "@/types/outcomeMeasures"
import {
  severityBadgeClasses,
  tripsSafetySignal,
  type InstrumentMeta,
} from "@/lib/outcomeMeasures"

interface OutcomeMeasureTrendProps {
  meta: InstrumentMeta
  /** Administrations for this instrument, ordered by administered_at ascending. */
  measures: OutcomeMeasure[]
  onDelete: (measure: OutcomeMeasure) => void
  deletingId?: string | null
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

/** Inline sparkline of complete administrations (those with a numeric total). */
function Sparkline({
  points,
  max,
}: {
  points: { total: number }[]
  max: number
}) {
  if (points.length < 2 || max <= 0) return null
  const W = 240
  const H = 40
  const pad = 4
  const stepX = (W - pad * 2) / (points.length - 1)
  const coords = points.map((p, i) => {
    const x = pad + i * stepX
    const y = pad + (H - pad * 2) * (1 - p.total / max)
    return { x, y }
  })
  const path = coords.map((c) => `${c.x},${c.y}`).join(" ")
  const last = coords[coords.length - 1]
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="h-10 w-full max-w-[240px]"
      role="img"
      aria-label="Score trend"
      preserveAspectRatio="none"
    >
      <polyline
        points={path}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        className="text-primary-500"
        vectorEffect="non-scaling-stroke"
      />
      <circle cx={last.x} cy={last.y} r={2.5} className="fill-primary-600" />
    </svg>
  )
}

export function OutcomeMeasureTrend({
  meta,
  measures,
  onDelete,
  deletingId,
}: OutcomeMeasureTrendProps) {
  if (measures.length === 0) return null

  const completePoints = measures
    .filter((m) => m.total_score !== null)
    .map((m) => ({ total: m.total_score as number }))
  const latest = measures[measures.length - 1]

  return (
    <div className="space-y-3 rounded-lg border border-neutral-100 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-base font-semibold text-neutral-900">
            {meta.displayName}
          </h3>
          <p className="text-xs text-neutral-500">
            {measures.length}{" "}
            {measures.length === 1 ? "administration" : "administrations"}
          </p>
        </div>
        {latest.total_score !== null && (
          <div className="text-right">
            <div className="text-2xl font-semibold text-neutral-900">
              {latest.total_score}
            </div>
            {latest.severity && (
              <span
                className={`inline-flex rounded px-2 py-0.5 text-xs font-medium capitalize ${severityBadgeClasses(
                  latest.severity,
                )}`}
              >
                {latest.severity}
              </span>
            )}
          </div>
        )}
      </div>

      <Sparkline points={completePoints} max={meta.items.length * 3} />

      <ul className="divide-y divide-neutral-100">
        {[...measures].reverse().map((m) => {
          const tripped = tripsSafetySignal(meta, m.item_scores)
          return (
            <li
              key={m.id}
              className="flex items-center justify-between gap-3 py-2 text-sm"
            >
              <span className="flex min-w-0 items-center gap-2">
                <span className="text-neutral-700">
                  {formatDate(m.administered_at)}
                </span>
                {tripped && meta.safetySignal && (
                  <span
                    title={meta.safetySignal.label}
                    className="inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800"
                  >
                    <AlertTriangle className="h-3 w-3" />
                    Item {meta.safetySignal.itemKey}
                  </span>
                )}
              </span>
              <span className="flex shrink-0 items-center gap-3">
                <span className="font-semibold text-neutral-900">
                  {m.total_score ?? "—"}
                </span>
                {m.severity && (
                  <span
                    className={`hidden rounded px-2 py-0.5 text-xs font-medium capitalize sm:inline-flex ${severityBadgeClasses(
                      m.severity,
                    )}`}
                  >
                    {m.severity}
                  </span>
                )}
                <button
                  type="button"
                  onClick={() => onDelete(m)}
                  disabled={deletingId === m.id}
                  aria-label="Delete score"
                  className="text-neutral-300 transition-colors hover:text-red-500 disabled:opacity-40"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
