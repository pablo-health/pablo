// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useRef, useState } from "react"
import { format, isSameDay, isToday } from "date-fns"
import type { FloatingBlock } from "@/types/availability"
import { minutesSinceMidnight } from "./dateUtils"
import { zonedInstant } from "./schedule"
import { clampToViewport } from "./viewportClamp"

const MENU_WIDTH = 232
const MENU_HEIGHT = 210
/** How tall the put-back marker of a skipped block is, in px. */
const SKIPPED_MARKER_PX = 20

/** What the bands can ask for. Each is one date of one floating block. */
export interface FloatingBlockActions {
  onSkip: (block: FloatingBlock) => void
  onPutBack: (block: FloatingBlock) => void
  /** Pin the block to `start` ("HH:MM", the practice's clock) for its date. */
  onMoveTo: (block: FloatingBlock, start: string) => void
  /** A write is in flight; the controls wait for it. */
  pending?: boolean
}

interface FloatingBlockLayerProps {
  day: Date
  /** Floating blocks for the visible dates; this layer draws `day`'s. */
  blocks: FloatingBlock[]
  /** First hour the grid shows; bands are positioned from it. */
  dayStart: number
  rowHeightPx: number
  /** The practice's zone, which windows and pinned starts are kept in. */
  timeZone?: string
  actions: FloatingBlockActions
}

function practiceZone(timeZone?: string): string {
  if (timeZone) return timeZone
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
  } catch {
    return "UTC"
  }
}

function clockMinutes(clock: string): number {
  const [h, m] = clock.split(":").map(Number)
  return h * 60 + m
}

/** "14:00" as the practice reads it: "2:00 PM". */
function clockLabel(clock: string): string {
  const [h, m] = clock.split(":").map(Number)
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`
}

/** An instant on the practice's own clock, "HH:MM", for the time field. */
function practiceClock(iso: string, timeZone: string): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    hour: "2-digit",
    minute: "2-digit",
  }).formatToParts(new Date(iso))
  const field = (type: string) => parts.find((p) => p.type === type)?.value ?? "00"
  return `${field("hour")}:${field("minute")}`
}

/** Today's date on the practice's clock, "YYYY-MM-DD". */
function practiceToday(timeZone: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date())
}

/**
 * Time a floating block keeps free on each visible day, e.g. lunch.
 *
 * A floating or pinned block is drawn as a soft band where it sits today and
 * opens a small menu: skip it this day, set it to a time, or (once pinned)
 * let it float again. A skipped block keeps no time, so it gets no band, only
 * a slim marker at the top of its window to put it back. An unplaced block
 * (no stretch of its length left) draws nothing: there is nowhere to put it.
 */
export function FloatingBlockLayer({
  day,
  blocks,
  dayStart,
  rowHeightPx,
  timeZone,
  actions,
}: FloatingBlockLayerProps) {
  const zone = practiceZone(timeZone)
  // A past date can't be changed (the server refuses it), so its band is
  // drawn for the record and opens nothing.
  const today = practiceToday(zone)
  const top = (minute: number) => ((minute - dayStart * 60) / 60) * rowHeightPx
  return (
    <>
      {blocks.map((block) => {
        if ((block.state === "floating" || block.state === "pinned") && block.start && block.end) {
          if (!isSameDay(new Date(block.start), day)) return null
          const startMin = minutesSinceMidnight(block.start)
          const endMin = minutesSinceMidnight(block.end) || 24 * 60
          return (
            <FloatingBlockBand
              key={`${block.rule_id}-${block.date}`}
              block={block}
              day={day}
              timeZone={zone}
              actions={block.date < today ? undefined : actions}
              style={{
                top: top(startMin),
                height: Math.max(((endMin - startMin) / 60) * rowHeightPx - 2, 18),
                left: 2,
                right: 2,
              }}
            />
          )
        }
        if (block.state === "skipped" && block.date >= today) {
          const windowAt = zonedInstant(block.date, clockMinutes(block.window_start), zone)
          if (!isSameDay(new Date(windowAt), day)) return null
          const at = new Date(windowAt)
          return (
            <SkippedMarker
              key={`${block.rule_id}-${block.date}`}
              block={block}
              actions={actions}
              style={{
                top: top(at.getHours() * 60 + at.getMinutes()),
                height: SKIPPED_MARKER_PX,
                right: 4,
              }}
            />
          )
        }
        return null
      })}
    </>
  )
}

function FloatingBlockBand({
  block,
  day,
  timeZone,
  actions,
  style,
}: {
  block: FloatingBlock
  day: Date
  timeZone: string
  /** Absent on a past date: the band is only drawn. */
  actions?: FloatingBlockActions
  style: React.CSSProperties
}) {
  const [menuAt, setMenuAt] = useState<{ x: number; y: number } | null>(null)
  const start = new Date(block.start!)
  const end = new Date(block.end!)
  const range = `${format(start, "h:mm")} – ${format(end, "h:mm a")}`
  const name = `${block.label}, ${format(start, "h:mm a")} to ${format(end, "h:mm a")}`
  if (!actions) {
    return (
      <div
        data-testid="floating-block"
        data-state={block.state}
        aria-label={name}
        className="ed-floating-block pointer-events-none absolute z-[5] flex flex-col overflow-hidden rounded-md px-2 py-0.5 text-[11.5px]"
        style={style}
      >
        <span className="truncate font-semibold">{block.label}</span>
        <span className="truncate text-[10.5px]">{range}</span>
      </div>
    )
  }
  return (
    <>
      <button
        type="button"
        data-testid="floating-block"
        data-state={block.state}
        aria-haspopup="dialog"
        aria-expanded={menuAt !== null}
        aria-label={name}
        onClick={(event) => {
          // The column underneath books a new appointment on click.
          event.stopPropagation()
          const rect = event.currentTarget.getBoundingClientRect()
          setMenuAt({ x: rect.left, y: rect.bottom + 4 })
        }}
        className="ed-floating-block absolute z-[5] flex flex-col overflow-hidden rounded-md px-2 py-0.5 text-left text-[11.5px]"
        style={style}
      >
        <span className="truncate font-semibold">{block.label}</span>
        <span className="truncate text-[10.5px]">{range}</span>
      </button>
      {menuAt && (
        <FloatingBlockMenu
          block={block}
          day={day}
          timeZone={timeZone}
          actions={actions}
          x={menuAt.x}
          y={menuAt.y}
          onClose={() => setMenuAt(null)}
        />
      )}
    </>
  )
}

function SkippedMarker({
  block,
  actions,
  style,
}: {
  block: FloatingBlock
  actions: FloatingBlockActions
  style: React.CSSProperties
}) {
  return (
    <button
      type="button"
      data-testid="floating-block-skipped"
      onClick={(event) => {
        event.stopPropagation()
        if (!actions.pending) actions.onPutBack(block)
      }}
      className="ed-floating-block absolute z-[5] flex items-center rounded-full px-2 text-[10.5px] font-semibold opacity-80"
      style={style}
      aria-label={`Put back ${block.label}`}
    >
      Put back {block.label}
    </button>
  )
}

function FloatingBlockMenu({
  block,
  day,
  timeZone,
  actions,
  x,
  y,
  onClose,
}: {
  block: FloatingBlock
  day: Date
  timeZone: string
  actions: FloatingBlockActions
  x: number
  y: number
  onClose: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [moveTo, setMoveTo] = useState(() => practiceClock(block.start!, timeZone))

  useEffect(() => {
    const handlePointerDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("pointerdown", handlePointerDown)
    window.addEventListener("keydown", handleKeyDown)
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown)
      window.removeEventListener("keydown", handleKeyDown)
    }
  }, [onClose])

  const { left, top } = clampToViewport(x, y, MENU_WIDTH, MENU_HEIGHT)
  // A write already in flight swallows the press rather than greying the
  // controls out; the menu closes either way and the band redraws on refetch.
  const act = (run: () => void) => {
    onClose()
    if (!actions.pending) run()
  }

  return (
    <div
      ref={ref}
      role="dialog"
      aria-label={block.label}
      onClick={(event) => event.stopPropagation()}
      className="ed-dialog-in fixed z-[80] flex flex-col gap-1 p-2"
      style={{
        left,
        top,
        width: MENU_WIDTH,
        borderRadius: 12,
        backgroundColor: "var(--ed-canvas-elev)",
        boxShadow: "var(--ed-shadow-modal)",
        border: "1px solid var(--ed-hairline-strong)",
        color: "var(--ed-ink)",
      }}
    >
      <div className="px-1.5 pt-1">
        <div className="text-[13.5px] font-semibold">{block.label}</div>
        <div className="text-[11.5px]" style={{ color: "var(--ed-ink-soft)" }}>
          {block.state === "pinned"
            ? "Set for this day"
            : `Between ${clockLabel(block.window_start)} and ${clockLabel(block.window_end)}`}
        </div>
      </div>

      <button
        type="button"
        onClick={() => act(() => actions.onSkip(block))}
        className="ed-ctx-item rounded-lg px-2.5 py-2 text-left text-[13.5px] font-medium"
      >
        {isToday(day) ? "Skip today" : "Skip this day"}
      </button>

      {block.state === "pinned" && (
        <button
          type="button"
            onClick={() => act(() => actions.onPutBack(block))}
          className="ed-ctx-item rounded-lg px-2.5 py-2 text-left text-[13.5px] font-medium"
        >
          Put back
        </button>
      )}

      <form
        className="flex items-center gap-2 px-1.5 pb-1 pt-1"
        onSubmit={(event) => {
          event.preventDefault()
          if (/^\d{2}:\d{2}$/.test(moveTo)) act(() => actions.onMoveTo(block, moveTo))
        }}
      >
        <label className="text-[12px]" htmlFor={`move-${block.rule_id}`}>
          Move to
        </label>
        <input
          id={`move-${block.rule_id}`}
          type="time"
          value={moveTo}
          onChange={(event) => setMoveTo(event.target.value)}
          className="min-w-0 flex-1 rounded-md px-1.5 py-1 text-[12.5px]"
          style={{
            backgroundColor: "var(--ed-field-bg)",
            border: "1px solid var(--ed-field-border)",
            color: "var(--ed-ink)",
          }}
        />
        <button
          type="submit"
            className="rounded-md px-2 py-1 text-[12px] font-semibold"
          style={{ backgroundColor: "var(--ed-cta-bg)", color: "var(--ed-cta-fg)" }}
        >
          Move
        </button>
      </form>
    </div>
  )
}
