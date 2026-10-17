// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Image from "next/image"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { sayPeople } from "@/lib/peopleTerm"
import { dayNumber, pabloNoteLines, pickNoteLine } from "./pabloNoteLines"
import { usePabloNoteVisibility } from "./pabloNoteVisibility"

/**
 * One line a day from Pablo about something in the product. The lines and
 * their gates live in `pabloNoteLines.ts`; this only renders the day's pick.
 */
export function PabloNote() {
  const isVisible = usePabloNoteVisibility()
  const people = usePeopleTerm()
  const line = pickNoteLine(pabloNoteLines(), isVisible, dayNumber())

  if (!line) return null

  return (
    <div className="px-3 pb-2">
      <div className="flex items-start gap-2.5 rounded-lg bg-neutral-100/70 p-3">
        <Image
          src="/pablo-sidebar.webp"
          alt="Pablo"
          width={32}
          height={32}
          className="shrink-0 rounded-full"
        />
        <p className="text-xs leading-snug text-neutral-600" data-testid="pablo-note" data-line-id={line.id}>
          <span className="font-medium text-neutral-800">Pablo:</span>{" "}
          {sayPeople(line.text, people)}
        </p>
      </div>
    </div>
  )
}
