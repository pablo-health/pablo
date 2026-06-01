// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useRef } from "react"
import type { ReactNode } from "react"
import type { AppointmentResponse } from "@/types/scheduling"

/** Click/double-click disambiguation window (ms). A single click is deferred
 * this long so a double-click can cancel it before the peek opens. */
const CLICK_DELAY_MS = 220

interface EditorialEventWrapperProps {
  appointment: AppointmentResponse
  /** Single click → open the peek popover, anchored to this event's rect. */
  onPeek: (appointment: AppointmentResponse, anchorRect: DOMRect) => void
  /** Double click → open the edit flow. */
  onEdit: (appointment: AppointmentResponse) => void
  /** The positioned event card. */
  children: ReactNode
  className?: string
  style?: React.CSSProperties
}

/**
 * Wraps a positioned event card and disambiguates single click (peek) from
 * double click (edit) with a {@link CLICK_DELAY_MS} timer, so a single click
 * never fires its peek before a double-click has a chance to cancel it.
 *
 * Shared by week/day columns (month chips disambiguate inline since they are
 * plain buttons without absolute positioning).
 */
export function EditorialEventWrapper({
  appointment,
  onPeek,
  onEdit,
  children,
  className,
  style,
}: EditorialEventWrapperProps) {
  const ref = useRef<HTMLDivElement>(null)
  const clickTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (clickTimer.current) clearTimeout(clickTimer.current)
    }
  }, [])

  const handleClick = (e: React.MouseEvent<HTMLDivElement>) => {
    e.stopPropagation()
    if (clickTimer.current) clearTimeout(clickTimer.current)
    const rect = ref.current?.getBoundingClientRect()
    if (!rect) return
    clickTimer.current = setTimeout(() => {
      clickTimer.current = null
      onPeek(appointment, rect)
    }, CLICK_DELAY_MS)
  }

  const handleDoubleClick = (e: React.MouseEvent<HTMLDivElement>) => {
    e.stopPropagation()
    if (clickTimer.current) {
      clearTimeout(clickTimer.current)
      clickTimer.current = null
    }
    onEdit(appointment)
  }

  return (
    <div
      ref={ref}
      data-event="1"
      onClick={handleClick}
      onDoubleClick={handleDoubleClick}
      className={className}
      style={style}
    >
      {children}
    </div>
  )
}
