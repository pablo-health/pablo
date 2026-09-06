// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { TimeSlot } from "@/types/availability"
import { computeUnavailableGaps } from "./unavailability"

interface UnavailableLayerProps {
  slots: TimeSlot[]
  dayStartHour: number
  dayEndHour: number
  rowHeightPx: number
}

/**
 * Background shading for the complement of the free slots on this day —
 * an approximation for an assumed duration, not a promise (see
 * unavailability.ts). Always `pointer-events: none` and unstacked (no
 * explicit z-index) so it paints behind sibling event cards (which set
 * `z-10`) and never intercepts the slot-click handler on the canvas
 * beneath it.
 */
export function UnavailableLayer({
  slots,
  dayStartHour,
  dayEndHour,
  rowHeightPx,
}: UnavailableLayerProps) {
  const windowStart = dayStartHour * 60
  const gaps = computeUnavailableGaps(slots, dayStartHour, dayEndHour)
  return (
    <>
      {gaps.map((gap) => (
        <div
          key={gap.startMin}
          aria-hidden
          className="ed-unavailable pointer-events-none absolute left-0 right-0"
          style={{
            top: ((gap.startMin - windowStart) / 60) * rowHeightPx,
            height: ((gap.endMin - gap.startMin) / 60) * rowHeightPx,
          }}
        />
      ))}
    </>
  )
}
