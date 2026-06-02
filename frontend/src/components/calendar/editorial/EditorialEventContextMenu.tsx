// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useRef } from "react"
import { Check, Pencil } from "lucide-react"
import type {
  AppointmentResponse,
  AppointmentStatus,
} from "@/types/scheduling"
import { editorialStatusMeta } from "./status"
import { clampToViewport } from "./viewportClamp"

/** Menu dimensions used for viewport clamping. */
const MENU_WIDTH = 218
const MENU_HEIGHT = 230

/** The four statuses a therapist can flag straight from the menu. */
const STATUS_ORDER: AppointmentStatus[] = [
  "confirmed",
  "completed",
  "no_show",
  "cancelled",
]

interface EditorialEventContextMenuProps {
  appointment: AppointmentResponse
  /** Cursor position (clientX/clientY) where the right-click landed. */
  x: number
  y: number
  onClose: () => void
  /** Persist a new status via the update mutation. */
  onSetStatus: (appointment: AppointmentResponse, status: AppointmentStatus) => void
  /** Hand off to the existing edit flow. */
  onEdit: (appointment: AppointmentResponse) => void
}

/** Clamp the fixed-position menu into the viewport. */
function clampedPosition(x: number, y: number): { left: number; top: number } {
  return clampToViewport(x, y, MENU_WIDTH, MENU_HEIGHT)
}

/**
 * Right-click "Mark as" menu — flags an appointment confirmed / completed /
 * no-show / cancelled without opening the editor, plus an "Edit details…"
 * handoff. Closes on outside pointerdown, Escape, or window blur.
 */
export function EditorialEventContextMenu({
  appointment,
  x,
  y,
  onClose,
  onSetStatus,
  onEdit,
}: EditorialEventContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handlePointerDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("pointerdown", handlePointerDown)
    window.addEventListener("keydown", handleKeyDown)
    window.addEventListener("blur", onClose)
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown)
      window.removeEventListener("keydown", handleKeyDown)
      window.removeEventListener("blur", onClose)
    }
  }, [onClose])

  const { left, top } = clampedPosition(x, y)

  return (
    <div
      ref={ref}
      role="menu"
      aria-label="Change appointment status"
      onContextMenu={(e) => e.preventDefault()}
      className="ed-dialog-in fixed z-[80] p-1.5"
      style={{
        left,
        top,
        width: MENU_WIDTH,
        borderRadius: 12,
        backgroundColor: "var(--ed-canvas-elev)",
        boxShadow: "var(--ed-shadow-modal)",
        border: "1px solid var(--ed-hairline-strong)",
      }}
    >
      <div
        className="px-2.5 pb-1.5 pt-2 text-[11px] font-semibold uppercase tracking-[0.12em]"
        style={{ color: "var(--ed-ink-soft)" }}
      >
        Mark as
      </div>

      {STATUS_ORDER.map((status) => {
        const meta = editorialStatusMeta(status)
        const current = appointment.status === status
        return (
          <button
            key={status}
            type="button"
            role="menuitemradio"
            aria-checked={current}
            onClick={() => {
              onClose()
              onSetStatus(appointment, status)
            }}
            className="ed-ctx-item flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13.5px]"
            style={{
              fontWeight: current ? 700 : 500,
              color: "var(--ed-ink)",
              backgroundColor: current ? "var(--ed-pill-hover)" : "transparent",
            }}
          >
            <span
              aria-hidden
              className="shrink-0"
              style={{
                width: 9,
                height: 9,
                borderRadius: 999,
                backgroundColor: meta.rail,
              }}
            />
            {meta.label}
            {current && (
              <Check
                className="ml-auto h-3.5 w-3.5"
                strokeWidth={2.6}
                style={{ color: "var(--ed-ink-soft)" }}
              />
            )}
          </button>
        )
      })}

      <div
        className="mx-1.5 my-1.5"
        style={{ height: 1, backgroundColor: "var(--ed-hairline)" }}
        aria-hidden
      />

      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onClose()
          onEdit(appointment)
        }}
        className="ed-ctx-item flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13.5px] font-medium"
        style={{ color: "var(--ed-ink)" }}
      >
        <Pencil className="h-3.5 w-3.5 shrink-0" style={{ color: "var(--ed-ink-soft)" }} />
        Edit details…
      </button>
    </div>
  )
}
