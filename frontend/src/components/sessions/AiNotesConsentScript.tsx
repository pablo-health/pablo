// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useAiNotesConsentSetting } from "@/hooks/useAiConsent"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"

/** A retention window as someone would say it: "1 year", "2 years", "90 days". */
export function formatRetention(days: number): string {
  if (days % 365 === 0) {
    const years = days / 365
    return years === 1 ? "1 year" : `${years} years`
  }
  return days === 1 ? "1 day" : `${days} days`
}

/**
 * What the clinician reads aloud before recording. Spoken to the client, so
 * it says "you" and needs no word for them.
 *
 * Four things and nothing else: what is recorded, that AI drafts the note and
 * the clinician reviews it, how long the audio is kept (the practice's stored
 * window, whether or not its control is shown in Settings), and that the
 * client can say no at any time.
 */
export function consentScript(retentionDays: number): string[] {
  return [
    "I'd like to record our session today.",
    "The recording is turned into a written transcript, and an AI tool uses it to draft my notes. I read and correct every note myself.",
    `The audio is kept for ${formatRetention(retentionDays)}, then deleted.`,
    "You can say no, now or at any time.",
    "Is that all right with you?",
  ]
}

/**
 * A button that opens the read-aloud script. Renders nothing when the practice
 * does not ask clients, or before the setting has loaded.
 */
export function AiNotesConsentScriptButton({ className }: { className?: string }) {
  const { data: setting } = useAiNotesConsentSetting()
  const [open, setOpen] = useState(false)
  const people = usePeopleTerm()

  if (!setting?.ask_clients_about_ai_notes) return null

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={className ?? "text-sm text-primary-700 hover:underline"}
      >
        Consent script
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Asking about AI-assisted notes</DialogTitle>
            <DialogDescription>Read this aloud before you start recording.</DialogDescription>
          </DialogHeader>
          <blockquote
            data-testid="ai-notes-consent-script"
            className="space-y-2 border-l-2 border-primary-200 pl-4 text-[15px] leading-relaxed text-neutral-900"
          >
            {consentScript(setting.audio_retention_days).map((line) => (
              <p key={line}>{line}</p>
            ))}
          </blockquote>
          <p className="text-sm text-neutral-600">
            Record the {people.one}&apos;s answer on their chart.
          </p>
        </DialogContent>
      </Dialog>
    </>
  )
}
