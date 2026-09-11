// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * First-run capture of a practice's general hours, shown the first time
 * the calendar is opened with no availability rules at all.
 *
 * It comes before connecting Google on purpose: free/busy sync has
 * nothing to sit in until some hours exist, and offers, reminders and
 * self-booking all key off availability rules — so a practice that
 * finishes Google setup with zero rules has a connected calendar that
 * still cannot offer a time.
 *
 * Parser first: one plain-language box, with example chips that fill it.
 * Every parse is echoed back as a plain-language summary the practice
 * edits and confirms — nothing is written until they do, because a
 * parser, unlike a form, can misunderstand quietly. The shared
 * `WorkingHoursGrid` stays one click away and takes over automatically
 * when the parser is unavailable or twice unsure, so a model being down
 * can never block a practice.
 */

import { useCallback, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { SetupStepHead } from "@/components/setup"
import {
  DEFAULT_WORKING_HOURS,
  WorkingHoursGrid,
  isCompleteSelection,
  selectionFromRules,
  workingHoursRules,
  type WorkingHoursSelection,
} from "@/components/availability/WorkingHoursGrid"
import { echoLines, timezoneOptions, type EchoLine } from "./hoursCapture"
import { useCreateAvailabilityRule, useParseAvailabilityRules } from "@/hooks/useAvailability"
import { detectBrowserTimezone, usePreferences, useSavePreferences } from "@/hooks/usePreferences"
import type {
  CreateAvailabilityRuleRequest,
  ProposedAvailabilityRule,
} from "@/types/availability"

/** They double as documentation of what the box understands — a blank box
 * is the classic way natural-language input fails. */
const EXAMPLES = [
  "I see clients Monday to Thursday, 9 to 5",
  "No appointments before 10am",
  "Two intakes a week, Tuesdays only",
  "Fridays are admin, no clients",
] as const

/** Two parses it could not pin down is enough: stop asking a practice to
 * rephrase and hand them the grid. */
const UNSURE_LIMIT = 2

const PARSER_DOWN =
  "Pablo could not read that just now, so here is the grid instead — it saves the same hours."

const PARSER_UNSURE =
  "Pablo is not sure it understood, so here is the grid instead — it saves the same hours."

const GENERIC_UNSURE =
  "Pablo could not turn that into hours. Try naming the days and the times, or use the grid."

const SAVE_ERROR = "Those hours could not be saved. Nothing was changed — try again."

const SKIP_CONSEQUENCE =
  "Until Pablo knows your hours it cannot offer times to a client, send session reminders, or let anyone book themselves."

interface CalendarHoursStepProps {
  /** Rules were created — the host moves on (to Google, or the calendar). */
  onSaved: () => void
  /** Left without creating anything. */
  onSkip: () => void
}

export function CalendarHoursStep({ onSaved, onSkip }: CalendarHoursStepProps) {
  const { data: preferences } = usePreferences()
  const savePreferences = useSavePreferences()
  const createRule = useCreateAvailabilityRule()
  const parseRules = useParseAvailabilityRules()
  const boxRef = useRef<HTMLTextAreaElement>(null)

  const [text, setText] = useState("")
  const [proposals, setProposals] = useState<ProposedAvailabilityRule[] | null>(null)
  const [kept, setKept] = useState<boolean[]>([])
  const [unsure, setUnsure] = useState<string | null>(null)
  const [unsureCount, setUnsureCount] = useState(0)
  const [onGrid, setOnGrid] = useState(false)
  const [fallbackReason, setFallbackReason] = useState<string | null>(null)
  const [selection, setSelection] = useState<WorkingHoursSelection>(DEFAULT_WORKING_HOURS)
  const [timezone, setTimezone] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const detected = preferences?.timezone || detectBrowserTimezone()
  const chosenTimezone = timezone ?? detected

  // Typing and clicking a chip are the same path: the chip only fills the
  // box, and this is what reads it.
  const check = useCallback(async () => {
    const sentence = text.trim()
    if (!sentence || parseRules.isPending) return
    setError(null)
    setUnsure(null)
    try {
      const result = await parseRules.mutateAsync({ text: sentence })
      if (result.proposals.length === 0) {
        // The parser refuses rather than guesses when it is unsure, and
        // says why in the therapist's own words.
        const count = unsureCount + 1
        setUnsureCount(count)
        if (count >= UNSURE_LIMIT) {
          setFallbackReason(PARSER_UNSURE)
          setOnGrid(true)
          return
        }
        setUnsure(result.could_not_parse ?? GENERIC_UNSURE)
        return
      }
      setUnsureCount(0)
      setProposals(result.proposals)
      setKept(result.proposals.map(() => true))
    } catch {
      setFallbackReason(PARSER_DOWN)
      setOnGrid(true)
    }
  }, [parseRules, text, unsureCount])

  const fillExample = useCallback((example: string) => {
    setText(example)
    setUnsure(null)
    boxRef.current?.focus()
  }, [])

  const save = useCallback(
    async (rules: CreateAvailabilityRuleRequest[]) => {
      if (rules.length === 0 || saving) return
      setSaving(true)
      setError(null)
      try {
        for (const rule of rules) {
          await createRule.mutateAsync(rule)
        }
        // Confirming a zone the practice can see beats leaving the one
        // the browser happened to detect while they were travelling.
        if (preferences && chosenTimezone !== preferences.timezone) {
          await savePreferences.mutateAsync({ ...preferences, timezone: chosenTimezone })
        }
        onSaved()
      } catch {
        setError(SAVE_ERROR)
        setSaving(false)
      }
    },
    [chosenTimezone, createRule, onSaved, preferences, savePreferences, saving]
  )

  const lines = proposals ? echoLines(proposals) : []
  const keptRules = proposals
    ? proposals
        .filter((_, index) => kept[index])
        .map(({ rule_type, enforcement, params }) => ({ rule_type, enforcement, params }))
    : []

  function toggleLine(line: EchoLine) {
    const next = [...kept]
    const dropping = line.indexes.every((index) => next[index])
    for (const index of line.indexes) next[index] = !dropping
    setKept(next)
  }

  function startOver() {
    setProposals(null)
    setKept([])
    setUnsure(null)
  }

  const timezoneField = (
    <div className="grid gap-2">
      <Label htmlFor="hours-timezone">Times are in</Label>
      <Select value={chosenTimezone} onValueChange={setTimezone} disabled={saving}>
        <SelectTrigger id="hours-timezone" className="w-72">
          <SelectValue />
        </SelectTrigger>
        <SelectContent className="max-h-72">
          {timezoneOptions(chosenTimezone).map((zone) => (
            <SelectItem key={zone} value={zone}>
              {zone.replace(/_/g, " ")}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p className="text-xs text-muted-foreground">
        Detected from this browser. Change it if that is not where you practise.
      </p>
    </div>
  )

  const skipLink = (
    <button
      type="button"
      onClick={onSkip}
      disabled={saving}
      className="text-sm font-medium text-muted-foreground underline underline-offset-2"
    >
      Skip for now
    </button>
  )

  return (
    <div className="space-y-6">
      <SetupStepHead
        eyebrow="Your hours"
        title="When do you see clients?"
        lede="Pablo needs your general hours before it can offer a time, remind anyone about a session, or let a client book themselves."
      />

      {onGrid ? (
        <div className="space-y-6">
          {fallbackReason ? (
            <p className="text-sm text-neutral-600" role="status">
              {fallbackReason}
            </p>
          ) : null}

          <WorkingHoursGrid value={selection} onChange={setSelection} disabled={saving} />
          {timezoneField}

          {error ? (
            <p className="text-sm text-red-600" role="alert">
              {error}
            </p>
          ) : null}

          <div className="flex items-center gap-4">
            <Button
              type="button"
              onClick={() => save(workingHoursRules(selection))}
              disabled={!isCompleteSelection(selection) || saving}
            >
              Save these hours
            </Button>
            {fallbackReason ? null : (
              <button
                type="button"
                onClick={() => setOnGrid(false)}
                disabled={saving}
                className="text-sm font-medium text-muted-foreground underline underline-offset-2"
              >
                Describe them instead
              </button>
            )}
            {skipLink}
          </div>
        </div>
      ) : proposals ? (
        <div className="space-y-6">
          <div className="rounded-xl border border-border bg-muted/30 p-4">
            <p className="text-sm font-medium text-neutral-900">Got it:</p>
            <ul className="mt-2 space-y-2">
              {lines.map((line) => {
                const on = line.indexes.every((index) => kept[index])
                return (
                  <li key={line.indexes.join("-")} className="flex items-center gap-3">
                    <span
                      className={
                        on ? "text-sm text-neutral-800" : "text-sm text-neutral-400 line-through"
                      }
                    >
                      {line.text}
                    </span>
                    <button
                      type="button"
                      onClick={() => toggleLine(line)}
                      disabled={saving}
                      className="text-xs font-medium text-muted-foreground underline underline-offset-2"
                    >
                      {on ? "Remove" : "Put back"}
                    </button>
                  </li>
                )
              })}
            </ul>
            <p className="mt-3 text-xs text-muted-foreground">
              Nothing is saved until you say this is right.
            </p>
          </div>

          {timezoneField}

          {error ? (
            <p className="text-sm text-red-600" role="alert">
              {error}
            </p>
          ) : null}

          <div className="flex items-center gap-4">
            <Button
              type="button"
              onClick={() => save(keptRules)}
              disabled={keptRules.length === 0 || saving}
            >
              Yes, save this
            </Button>
            <button
              type="button"
              onClick={startOver}
              disabled={saving}
              className="text-sm font-medium text-muted-foreground underline underline-offset-2"
            >
              Say it differently
            </button>
            <button
              type="button"
              onClick={() => {
                setSelection(selectionFromRules(proposals) ?? DEFAULT_WORKING_HOURS)
                setOnGrid(true)
              }}
              disabled={saving}
              className="text-sm font-medium text-muted-foreground underline underline-offset-2"
            >
              Pick from a grid instead
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="grid gap-2">
            <Label htmlFor="hours-sentence">Tell Pablo in your own words</Label>
            <Textarea
              id="hours-sentence"
              ref={boxRef}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="I see clients Monday to Thursday, 9 to 5"
              rows={3}
              disabled={parseRules.isPending}
            />
          </div>

          <div className="flex flex-wrap gap-2">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => fillExample(example)}
                disabled={parseRules.isPending}
                className="rounded-full border border-border px-3 py-1 text-xs text-neutral-600 hover:bg-muted"
              >
                {example}
              </button>
            ))}
          </div>

          {unsure ? (
            <p className="text-sm text-neutral-600" role="status">
              {unsure}
            </p>
          ) : null}

          <div className="flex items-center gap-4">
            <Button type="button" onClick={check} disabled={!text.trim() || parseRules.isPending}>
              {parseRules.isPending ? "Reading…" : "Check this"}
            </Button>
            <button
              type="button"
              onClick={() => setOnGrid(true)}
              className="text-sm font-medium text-muted-foreground underline underline-offset-2"
            >
              Pick from a grid instead
            </button>
            {skipLink}
          </div>
        </div>
      )}

      <p className="text-xs text-muted-foreground">{SKIP_CONSEQUENCE}</p>
    </div>
  )
}
