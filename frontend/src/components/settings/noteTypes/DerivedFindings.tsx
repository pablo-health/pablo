// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Check, Plus } from "lucide-react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import type { DeriveNoteTypeResponse, DeriveSuggestion } from "@/types/noteTypes"
import { SettingsCard } from "../ui"

interface DerivedFindingsProps {
  derived: DeriveNoteTypeResponse
  /** Add a blank field for an unplaced passage; returns the section it went into. */
  onAddField: () => string
  /** Add a section for something the reference has and the proposal lacks. */
  onAddSection: (suggestion: DeriveSuggestion) => void
}

type Placed = Record<string, string>

/**
 * What the proposal left out of the clinician's own notes, and what the chosen
 * comparison has that it lacks, beside the editor holding the proposal.
 *
 * "Add a field for this" adds a blank field and never copies the passage into
 * it: a definition is stored and shared across the practice, and a passage is
 * a client's record. The clinician names the field from what they see here.
 */
export function DerivedFindings({ derived, onAddField, onAddSection }: DerivedFindingsProps) {
  const [placed, setPlaced] = useState<Placed>({})
  const [handled, setHandled] = useState<Record<string, "added" | "ignored">>({})

  const { coverage, suggestions, reference, guard } = derived
  const withUnplaced = coverage.filter((c) => c.unplaced.length > 0)
  const unchecked = coverage.filter((c) => !c.checked)
  const excluded = coverage.flatMap((c) => c.excluded ?? [])
  const allPlaced = coverage.length > 0 && unchecked.length === 0 && withUnplaced.length === 0
  const openSuggestions = suggestions.filter((s) => handled[s.label] !== "ignored")

  return (
    <SettingsCard title="From your notes" description="Proposed from your notes. Change anything below, try it, then save.">
      <div className="space-y-4">
        {allPlaced && <p className="text-[13px] text-foreground">Everything in your notes has a field.</p>}

        {withUnplaced.map((c) => (
          <div key={c.sample}>
            <h3 className="mb-1.5 text-[13px] font-semibold text-foreground">
              {coverage.length > 1 ? `Not in a field yet, from note ${c.sample + 1}` : "Not in a field yet"}
            </h3>
            <ul className="space-y-2">
              {c.unplaced.map((passage, pi) => {
                const id = `${c.sample}:${pi}`
                return (
                  <li
                    key={id}
                    data-testid="unplaced-passage"
                    className="flex items-start justify-between gap-3 rounded-lg border border-amber-300/70 bg-amber-50/60 px-3 py-2 dark:border-amber-500/40 dark:bg-amber-500/10"
                  >
                    <p className="min-w-0 whitespace-pre-wrap text-[13px] text-foreground">{passage}</p>
                    {placed[id] ? (
                      <span className="flex shrink-0 items-center gap-1 text-[12.5px] text-secondary-600">
                        <Check className="h-3.5 w-3.5" aria-hidden="true" />
                        Field added to {placed[id]}
                      </span>
                    ) : (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="shrink-0"
                        onClick={() => setPlaced({ ...placed, [id]: onAddField() })}
                      >
                        <Plus aria-hidden="true" />
                        Add a field for this
                      </Button>
                    )}
                  </li>
                )
              })}
            </ul>
          </div>
        ))}

        {unchecked.map((c) => (
          <p key={c.sample} className="text-[12.5px] text-muted-foreground">
            Note {c.sample + 1} couldn&apos;t be checked against this note type.
          </p>
        ))}

        {excluded.length > 0 && (
          <details>
            <summary className="cursor-pointer text-[12.5px] text-muted-foreground">
              Lines that aren&apos;t note content, such as signatures ({excluded.length})
            </summary>
            <ul className="mt-2 space-y-1 border-l-2 border-border pl-3">
              {excluded.map((line, i) => (
                <li key={i} className="whitespace-pre-wrap text-[12.5px] text-muted-foreground">
                  {line}
                </li>
              ))}
            </ul>
          </details>
        )}

        {guard.length > 0 && (
          <p className="text-[12.5px] text-muted-foreground">Wording that repeated your notes was replaced.</p>
        )}

        {reference && openSuggestions.length > 0 && (
          <div>
            <h3 className="mb-1.5 text-[13px] font-semibold text-foreground">{reference.label} also has</h3>
            <ul className="space-y-2">
              {openSuggestions.map((s) => (
                <li
                  key={s.label}
                  data-testid="reference-suggestion"
                  className="flex items-start justify-between gap-3 rounded-lg border border-border px-3 py-2"
                >
                  <div className="min-w-0">
                    <div className="text-[13px] font-semibold text-foreground">{s.label}</div>
                    {s.description && <div className="text-[12.5px] text-muted-foreground">{s.description}</div>}
                  </div>
                  {handled[s.label] === "added" ? (
                    <span className="flex shrink-0 items-center gap-1 text-[12.5px] text-secondary-600">
                      <Check className="h-3.5 w-3.5" aria-hidden="true" />
                      Section added
                    </span>
                  ) : (
                    <div className="flex shrink-0 items-center gap-1">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          onAddSection(s)
                          setHandled({ ...handled, [s.label]: "added" })
                        }}
                      >
                        Add as a section
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => setHandled({ ...handled, [s.label]: "ignored" })}
                      >
                        Ignore
                      </Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </SettingsCard>
  )
}
