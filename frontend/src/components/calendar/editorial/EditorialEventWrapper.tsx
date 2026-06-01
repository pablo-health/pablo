// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useRef } from "react"
import type { ReactNode } from "react"
import type { AppointmentResponse } from "@/types/scheduling"
import {
  DRAG_THRESHOLD_PX,
  rescheduledStart,
  snapDragDelta,
} from "./dragSnap"

/** Click/double-click disambiguation window (ms). A single click is deferred
 * this long so a double-click can cancel it before the peek opens. */
const CLICK_DELAY_MS = 220

/** How long the post-drop click-suppression flag lingers (ms). A real pointer
 * drop fires a trailing `click`; this window lets us swallow it so the drag
 * doesn't also open the peek. */
const DRAG_CLICK_SUPPRESS_MS = 60

interface DragConfig {
  /** "week" allows horizontal day shifts; "day" is vertical-only. */
  mode: "week" | "day"
  /** Height of one hour row in px (vertical snap reference). */
  rowHeightPx: number
  /** CSS selector for the positioning grid; its width / 7 is the column width. */
  gridSelector: string
  /** Reschedule mutation — original duration is preserved, start clamped to day. */
  onMove: (appointment: AppointmentResponse, newStartIso: string) => void
}

interface EditorialEventWrapperProps {
  appointment: AppointmentResponse
  /** Single click → open the peek popover, anchored to this event's rect. */
  onPeek: (appointment: AppointmentResponse, anchorRect: DOMRect) => void
  /** Double click → open the edit flow. */
  onEdit: (appointment: AppointmentResponse) => void
  /** Right click → open the status menu at the cursor (clientX/clientY). */
  onContextMenu?: (
    appointment: AppointmentResponse,
    x: number,
    y: number,
  ) => void
  /** Pointer-drag-to-reschedule config. Omit to disable dragging (e.g. month). */
  drag?: DragConfig
  /** The positioned event card. */
  children: ReactNode
  className?: string
  style?: React.CSSProperties
}

/**
 * Wraps a positioned event card and disambiguates single click (peek) from
 * double click (edit) with a {@link CLICK_DELAY_MS} timer, and — when {@link
 * DragConfig} is supplied — pointer-drag-to-reschedule. A press only becomes a
 * drag after the pointer travels past {@link DRAG_THRESHOLD_PX}, so clicks and
 * double-clicks still register.
 *
 * Shared by week/day columns (month chips disambiguate inline since they are
 * plain buttons without absolute positioning).
 */
export function EditorialEventWrapper({
  appointment,
  onPeek,
  onEdit,
  onContextMenu,
  drag,
  children,
  className,
  style,
}: EditorialEventWrapperProps) {
  const ref = useRef<HTMLDivElement>(null)
  const clickTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** Set immediately after a committed drop so the trailing click is ignored. */
  const justDragged = useRef(false)

  useEffect(() => {
    return () => {
      if (clickTimer.current) clearTimeout(clickTimer.current)
    }
  }, [])

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    // Left button only, so dragging never fights right-click / context menu.
    if (!drag || e.button !== 0) return
    const el = ref.current
    if (!el) return
    const grid = el.closest(drag.gridSelector)
    if (!grid) return

    const colWidthPx =
      drag.mode === "week" ? grid.getBoundingClientRect().width / 7 : 0
    const startX = e.clientX
    const startY = e.clientY
    let moved = false
    let minuteShift = 0
    let dayShift = 0

    try {
      el.setPointerCapture(e.pointerId)
    } catch {
      // setPointerCapture can throw if the pointer is already released; the
      // window-level listeners below still complete the drag.
    }

    const onMove = (ev: PointerEvent) => {
      const dx = ev.clientX - startX
      const dy = ev.clientY - startY
      if (!moved && Math.hypot(dx, dy) > DRAG_THRESHOLD_PX) {
        moved = true
        if (clickTimer.current) {
          clearTimeout(clickTimer.current)
          clickTimer.current = null
        }
        el.style.zIndex = "60"
        el.style.opacity = "0.92"
        el.style.cursor = "grabbing"
        el.style.boxShadow = "var(--ed-shadow-card-hover)"
      }
      if (!moved) return
      const snapped = snapDragDelta(
        dx,
        dy,
        drag.rowHeightPx,
        colWidthPx,
        drag.mode,
      )
      minuteShift = snapped.minuteShift
      dayShift = snapped.dayShift
      el.style.transform = `translate(${dayShift * colWidthPx}px, ${
        (minuteShift / 60) * drag.rowHeightPx
      }px)`
    }

    const onUp = () => {
      window.removeEventListener("pointermove", onMove)
      window.removeEventListener("pointerup", onUp)
      el.style.transform = ""
      el.style.zIndex = ""
      el.style.opacity = ""
      el.style.cursor = ""
      el.style.boxShadow = ""
      if (moved && (minuteShift !== 0 || dayShift !== 0)) {
        justDragged.current = true
        setTimeout(() => {
          justDragged.current = false
        }, DRAG_CLICK_SUPPRESS_MS)
        const newStart = rescheduledStart(
          appointment.start_at,
          appointment.duration_minutes,
          minuteShift,
          dayShift,
        )
        drag.onMove(appointment, newStart)
      }
    }

    window.addEventListener("pointermove", onMove)
    window.addEventListener("pointerup", onUp)
  }

  const handleClick = (e: React.MouseEvent<HTMLDivElement>) => {
    e.stopPropagation()
    // Swallow the click that trails a committed drag so it doesn't open peek.
    if (justDragged.current) return
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

  const handleContextMenu = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!onContextMenu) return
    // Suppress the native menu and a pending single-click peek; the custom
    // status menu opens at the cursor instead.
    e.preventDefault()
    e.stopPropagation()
    if (clickTimer.current) {
      clearTimeout(clickTimer.current)
      clickTimer.current = null
    }
    onContextMenu(appointment, e.clientX, e.clientY)
  }

  return (
    <div
      ref={ref}
      data-event="1"
      onPointerDown={handlePointerDown}
      onClick={handleClick}
      onDoubleClick={handleDoubleClick}
      onContextMenu={handleContextMenu}
      className={className}
      style={{ ...style, touchAction: drag ? "none" : undefined }}
    >
      {children}
    </div>
  )
}
