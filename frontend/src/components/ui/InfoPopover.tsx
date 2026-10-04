// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useId, useRef, useState } from "react"
import { Info } from "lucide-react"

import { cn } from "@/lib/utils"
import { Popover, PopoverContent, PopoverTrigger } from "./popover"

/**
 * A small "i" button that shows a short explanation beside a control.
 *
 * For detail a reader may want but does not need in order to act: how far a
 * permission reaches, say. Never for a consent condition, an error, or a
 * consequence the reader has to see before deciding — those stay on the page.
 *
 * It opens on a click or tap, on Enter or Space, and when the button takes
 * keyboard focus, so nothing depends on hovering. Escape, a click outside, or
 * tabbing away closes it.
 */
export function InfoPopover({
  label,
  children,
  className,
}: {
  /** The button's accessible name, such as "About this permission". */
  label: string
  children: React.ReactNode
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const textId = useId()
  const triggerRef = useRef<HTMLButtonElement>(null)
  // A mouse press or a tap focuses the button before it clicks it. When that
  // focus opened the popover, the click of the same press must not close it.
  const openedByFocus = useRef(false)
  // Escape hands focus back to the button, and that focus must not reopen it.
  const refocusing = useRef(false)
  const interactedOutside = useRef(false)

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (!next) openedByFocus.current = false
        setOpen(next)
      }}
    >
      <PopoverTrigger asChild>
        <button
          ref={triggerRef}
          type="button"
          aria-label={label}
          // Focus stays on the button while the text shows, so the text is
          // announced as the button's description.
          aria-describedby={open ? textId : undefined}
          onFocus={() => {
            if (refocusing.current) {
              refocusing.current = false
              return
            }
            if (!open) {
              openedByFocus.current = true
              setOpen(true)
            }
          }}
          onClick={(event) => {
            if (openedByFocus.current) {
              openedByFocus.current = false
              // Radix skips its own toggle for an event already handled.
              event.preventDefault()
            }
          }}
          onBlur={() => {
            openedByFocus.current = false
          }}
          className={cn(
            "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:text-neutral-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            className
          )}
        >
          <Info className="h-4 w-4" aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="text-sm text-neutral-800"
        // Leave focus on the button, so tabbing through the page carries on
        // from where it was.
        onOpenAutoFocus={(event) => event.preventDefault()}
        onInteractOutside={() => {
          interactedOutside.current = true
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          const trigger = triggerRef.current
          if (!interactedOutside.current && trigger && document.activeElement !== trigger) {
            refocusing.current = true
            trigger.focus()
          }
          interactedOutside.current = false
        }}
      >
        <div id={textId}>{children}</div>
      </PopoverContent>
    </Popover>
  )
}
