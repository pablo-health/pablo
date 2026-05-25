// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import Image from "next/image"

// A warm/funny/helpful line from Pablo. Mix of tips and morale.
const NOTES = [
  "Tip: ⌘ + ↵ finalizes a note.",
  "Psst — drag an appointment to reschedule it.",
  "Water break? Water break.",
  "One note at a time. You’ve got this.",
  "More looks live in Settings → Appearance.",
  "Remember to breathe between sessions.",
  "Today’s a good day to leave on time.",
]

// Thought of the day — deterministic so SSR and client agree (no flicker).
function noteOfTheDay(): string {
  const day = Math.floor(Date.now() / 86_400_000)
  return NOTES[day % NOTES.length]
}

export function PabloNote() {
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
        <p className="text-xs leading-snug text-neutral-600">
          <span className="font-medium text-neutral-800">Pablo:</span>{" "}
          {noteOfTheDay()}
        </p>
      </div>
    </div>
  )
}
