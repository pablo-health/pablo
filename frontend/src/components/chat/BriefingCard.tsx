// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Briefing card (§13.4) — sage-tinted Fraunces-italic empty state that
 * tells the user, in plain language, which chart artefacts the model
 * will read on the first turn.
 *
 * Composition rule (per design doc):
 *   "I'm reading {firstName}'s {comma-separated sources}. Ask me anything."
 *
 * Sources are filtered against the patient's notes list so a key that
 * resolves to ``row_count: 0`` (e.g. ``safety_plan_active`` for a
 * patient with no safety plan) is omitted — we never say "no safety
 * plan." That mirrors the manifest-preview semantics described in
 * §13.4 without requiring a separate backend preview endpoint.
 */

import { useMemo } from "react"

import { cn } from "@/lib/utils"
import { usePatient } from "@/hooks/usePatients"
import { usePatientNotes } from "@/hooks/useNotes"
import type { SourceKey, SourceSelection } from "@/lib/chat/types"
import type { Note } from "@/types/notes"

// Map each note-backed source key to the ``note_type`` values that
// represent it in ``backend/app/services/chat_context_bundler.py``.
// Kept here (rather than imported) because the FE Note type intentionally
// treats ``note_type`` as an open string and OSS doesn't ship a registry.
const NOTE_TYPE_GROUPS: Partial<Record<SourceKey, readonly string[]>> = {
  most_recent_intake: ["intake", "biopsychosocial"],
  treatment_plan_active: ["treatment_plan"],
  safety_plan_active: ["safety_plan", "stanley_brown"],
  progress_notes_recent: ["soap", "narrative"],
}

const DEFAULT_PROGRESS_LIMIT = 3

function formatDate(iso: string | undefined | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleDateString("en-US", { month: "long", day: "numeric" })
}

function progressLimit(selection: SourceSelection): number {
  const v = selection.progress_notes_recent
  if (v && typeof v === "object" && typeof v.limit === "number") {
    return v.limit
  }
  return DEFAULT_PROGRESS_LIMIT
}

function sortDescByCreatedAt(notes: Note[]): Note[] {
  return [...notes].sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
}

function buildSourcePhrases(
  selection: SourceSelection,
  notes: Note[],
): string[] {
  // ``pasted_text`` is intentionally omitted — the briefing card
  // describes what Pablo will pull from the chart. The user already
  // knows what they pasted in.
  const phrases: string[] = []

  if (selection.most_recent_intake) {
    const types = NOTE_TYPE_GROUPS.most_recent_intake!
    const match = sortDescByCreatedAt(
      notes.filter((n) => types.includes(n.note_type as string)),
    )[0]
    if (match) {
      const date = formatDate(match.created_at)
      phrases.push(
        date
          ? `the most recent intake from ${date}`
          : "the most recent intake",
      )
    }
  }

  if (selection.treatment_plan_active) {
    const types = NOTE_TYPE_GROUPS.treatment_plan_active!
    const has = notes.some((n) => types.includes(n.note_type as string))
    if (has) phrases.push("the active treatment plan")
  }

  if (selection.safety_plan_active) {
    const types = NOTE_TYPE_GROUPS.safety_plan_active!
    const has = notes.some((n) => types.includes(n.note_type as string))
    if (has) phrases.push("the active safety plan")
  }

  if (selection.current_medications) {
    phrases.push("the current medication list")
  }

  if (selection.progress_notes_recent) {
    const types = NOTE_TYPE_GROUPS.progress_notes_recent!
    const matches = sortDescByCreatedAt(
      notes.filter((n) => types.includes(n.note_type as string)),
    )
    if (matches.length > 0) {
      const requested = progressLimit(selection)
      const n = Math.min(requested, matches.length)
      const lastDate = formatDate(matches[0].created_at)
      const stem = `${n} most recent progress ${n === 1 ? "note" : "notes"}`
      phrases.push(lastDate ? `${stem} (last from ${lastDate})` : stem)
    }
  }

  if (selection.progress_notes_explicit) {
    phrases.push("the session notes you pinned")
  }

  if (selection.lab_values_recent) {
    phrases.push("recent lab values")
  }

  if (selection.vitals_recent) {
    phrases.push("recent vitals")
  }

  return phrases
}

/**
 * Strip a leading "the " from the head phrase so the patient-possessive
 * reads cleanly ("Maria's active treatment plan" vs the ungrammatical
 * "Maria's the active treatment plan"). Subsequent phrases keep their
 * determiner — the possessive only applies to the head.
 */
function dropLeadingThe(phrase: string): string {
  return phrase.replace(/^the\s+/i, "")
}

function joinWithOxfordAnd(parts: string[]): string {
  if (parts.length === 0) return ""
  const head = dropLeadingThe(parts[0])
  if (parts.length === 1) return head
  if (parts.length === 2) return `${head} and ${parts[1]}`
  const middle = parts.slice(1, -1).join(", ")
  return `${head}, ${middle}, and ${parts[parts.length - 1]}`
}

export interface BriefingCardProps {
  patientId: string
  selection: SourceSelection
  className?: string
}

export function BriefingCard({
  patientId,
  selection,
  className,
}: BriefingCardProps) {
  const patientQ = usePatient(patientId)
  const notesQ = usePatientNotes(patientId)

  const firstName = patientQ.data?.first_name?.trim() || "this patient"
  const notesData = notesQ.data?.data

  const sentence = useMemo(() => {
    const parts = buildSourcePhrases(selection, notesData ?? [])
    if (parts.length === 0) {
      return `I'm ready to chat about ${firstName}.`
    }
    return `I'm reading ${firstName}'s ${joinWithOxfordAnd(parts)}.`
  }, [firstName, notesData, selection])

  return (
    <div
      data-slot="chat-briefing-card"
      className={cn(
        "rounded-2xl border border-secondary-200 bg-secondary-50/60 px-5 py-4 shadow-[0_1px_0_rgba(0,0,0,0.02)]",
        className,
      )}
    >
      <p
        data-slot="chat-briefing-sentence"
        className="font-display italic text-[15px] leading-relaxed text-secondary-900"
      >
        {sentence}
      </p>
      <p
        data-slot="chat-briefing-invitation"
        className="mt-1.5 text-xs text-secondary-700"
      >
        Ask me anything.
      </p>
    </div>
  )
}
