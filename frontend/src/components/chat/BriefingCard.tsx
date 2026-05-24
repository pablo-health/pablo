// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Briefing card (§13.4) — sage-tinted Fraunces-italic empty state that
 * tells the user, in plain language, which chart artefacts the model
 * will read on the first turn.
 *
 * Reads truth from the backend: ``POST /api/chat/conversations/preview``
 * runs the same context bundler the streaming turn would and returns a
 * manifest with ``sources_included`` (per-source ``row_count`` +
 * ``latest_at``). The card composes its sentence directly from that
 * manifest — no FE-side replication of the bundler's note_type / date
 * logic, so this can't drift when the backend registers new note types
 * or reorders priorities.
 */

import { useQuery } from "@tanstack/react-query"
import { useMemo } from "react"

import { cn } from "@/lib/utils"
import { usePatient } from "@/hooks/usePatients"
import { previewChatContext } from "@/lib/chat/api"
import type {
  ContextManifest,
  ManifestIncludedEntry,
  SourceKey,
  SourceSelection,
} from "@/lib/chat/types"

function formatDate(iso: string | undefined | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleDateString("en-US", { month: "long", day: "numeric" })
}

function progressLimit(selection: SourceSelection): number | null {
  const v = selection.progress_notes_recent
  if (v && typeof v === "object" && typeof v.limit === "number") {
    return v.limit
  }
  return null
}

/**
 * Map each source key to the lay-friendly phrase that goes into the
 * briefing sentence. The first phrase in the rendered list gets its
 * leading "the " stripped so the patient-possessive reads cleanly
 * ("Maria's active treatment plan" vs the ungrammatical "Maria's the
 * active treatment plan").
 */
function phraseForSource(
  entry: ManifestIncludedEntry,
  selection: SourceSelection,
): string | null {
  if ((entry.row_count ?? 0) === 0 && entry.source_key !== "pasted_text") {
    // Spec §13.4: omit empty sources — never say "no safety plan."
    // ``pasted_text`` is the one exception: it never has a row_count
    // but we still drop it from the briefing (user knows what they
    // pasted in).
    return null
  }
  const lastDate = formatDate(entry.latest_at)
  switch (entry.source_key) {
    case "most_recent_intake":
      return lastDate
        ? `the most recent intake from ${lastDate}`
        : "the most recent intake"
    case "treatment_plan_active":
      return "the active treatment plan"
    case "safety_plan_active":
      return "the active safety plan"
    case "current_medications":
      return "the current medication list"
    case "progress_notes_recent": {
      const requested = progressLimit(selection)
      const n =
        requested !== null
          ? Math.min(requested, entry.row_count ?? requested)
          : (entry.row_count ?? 0)
      if (n <= 0) return null
      const stem = `${n} most recent progress ${n === 1 ? "note" : "notes"}`
      return lastDate ? `${stem} (last from ${lastDate})` : stem
    }
    case "progress_notes_explicit":
      return "the session notes you pinned"
    case "patient_documents": {
      const n = entry.row_count ?? 0
      if (n <= 0) return null
      return `${n} uploaded ${n === 1 ? "document" : "documents"}`
    }
    case "lab_values_recent":
      return "recent lab values"
    case "vitals_recent":
      return "recent vitals"
    case "pasted_text":
      return null
  }
}

function buildPhrases(
  manifest: ContextManifest | undefined,
  selection: SourceSelection,
): string[] {
  if (!manifest) return []
  const phrases: string[] = []
  for (const entry of manifest.sources_included) {
    const phrase = phraseForSource(entry, selection)
    if (phrase) phrases.push(phrase)
  }
  return phrases
}

/**
 * Strip a leading "the " from the head phrase so the possessive reads
 * cleanly. Subsequent phrases keep their determiner — the possessive
 * only attaches to the first item in the list.
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

const PREVIEW_STALE_MS = 60_000

export function BriefingCard({
  patientId,
  selection,
  className,
}: BriefingCardProps) {
  const patientQ = usePatient(patientId)

  // The selection dict is stable from the caller's POV but its JSON
  // form keys the query so a chip-rail toggle re-fetches the preview.
  const selectionKey = useMemo(() => JSON.stringify(selection), [selection])

  const previewQ = useQuery({
    queryKey: ["chat", "preview", patientId, selectionKey] as const,
    queryFn: () =>
      previewChatContext({
        patient_id: patientId,
        source_selection: selection,
      }),
    enabled: Boolean(patientId),
    staleTime: PREVIEW_STALE_MS,
  })

  const firstName = patientQ.data?.first_name?.trim() || "this patient"
  const manifest = previewQ.data?.manifest

  const sentence = useMemo(() => {
    const parts = buildPhrases(manifest, selection)
    if (parts.length === 0) {
      return `I'm ready to chat about ${firstName}.`
    }
    return `I'm reading ${firstName}'s ${joinWithOxfordAnd(parts)}.`
  }, [firstName, manifest, selection])

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

// Exported for unit-testing the sentence builder in isolation. Not part
// of the component's public surface.
export const __test = { buildPhrases, joinWithOxfordAnd, dropLeadingThe }
