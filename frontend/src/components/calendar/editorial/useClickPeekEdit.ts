// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { useEffect, useRef } from "react"
import type { AppointmentResponse } from "@/types/scheduling"

/** Click/double-click disambiguation window (ms). A single click is deferred
 * this long so a double-click can cancel it before the peek opens. */
export const CLICK_DELAY_MS = 220

interface UseClickPeekEditOptions {
  appointment: AppointmentResponse
  /** Fired (after {@link CLICK_DELAY_MS}) when a single click lands. */
  onPeek: (appointment: AppointmentResponse, anchorRect: DOMRect) => void
  /** Fired immediately on double-click (cancels the deferred peek). */
  onEdit: (appointment: AppointmentResponse) => void
  /** Fired on right-click (cancels the deferred peek). */
  onContextMenu?: (appointment: AppointmentResponse, x: number, y: number) => void
  /** Returns the rect used to anchor the peek popover. */
  getRect: () => DOMRect | undefined
  /** Set to true to prevent a pending peek after a drag. */
  justDragged?: React.MutableRefObject<boolean>
}

/**
 * Shared click/double-click/context-menu disambiguation logic consumed by both
 * {@link EditorialEventWrapper} (week/day) and MonthChip (month). Owns the
 * single source of truth for {@link CLICK_DELAY_MS} and the cancel-on-dblclick
 * pattern.
 */
export function useClickPeekEdit({
  appointment,
  onPeek,
  onEdit,
  onContextMenu,
  getRect,
  justDragged,
}: UseClickPeekEditOptions) {
  const clickTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (clickTimer.current) clearTimeout(clickTimer.current)
    }
  }, [])

  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (justDragged?.current) return
    if (clickTimer.current) clearTimeout(clickTimer.current)
    const rect = getRect()
    if (!rect) return
    clickTimer.current = setTimeout(() => {
      clickTimer.current = null
      onPeek(appointment, rect)
    }, CLICK_DELAY_MS)
  }

  const handleDoubleClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (clickTimer.current) {
      clearTimeout(clickTimer.current)
      clickTimer.current = null
    }
    onEdit(appointment)
  }

  const handleContextMenu = (e: React.MouseEvent) => {
    if (!onContextMenu) return
    e.preventDefault()
    e.stopPropagation()
    if (clickTimer.current) {
      clearTimeout(clickTimer.current)
      clickTimer.current = null
    }
    onContextMenu(appointment, e.clientX, e.clientY)
  }

  return { handleClick, handleDoubleClick, handleContextMenu }
}
