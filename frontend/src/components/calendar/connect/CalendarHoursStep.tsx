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
import {
  proposalToCreateRequest,
  type CreateAvailabilityRuleRequest,
  type ProposedAvailabilityRule,
} from "@/types/availability"
import { MissingAppointmentTypeOffer } from "@/components/availability/MissingAppointmentTypeOffer"
import { ReadingChoice } from "@/components/availability/ReadingChoice"
import { PabloSpinner } from "@/components/ui/PabloSpinner"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { sayPeople } from "@/lib/peopleTerm"
import type { ParseReading } from "@/types/availability"

/** They double as documentation of what the box understands — a blank box
 * is the classic way natural-language input fails. */
const EXAMPLES = [
  "I see {people} Monday to Thursday, 9 to 5",
  "No appointments before 10am",
  "Two intakes a week, Tuesdays only",
  "Fridays are admin, no {people}",
] as const

/** Two parses it could not pin down is enough: stop asking a practice to
 * rephrase and hand them the grid. */
const UNSURE_LIMIT = 2

const PARSER_DOWN = "Pablo couldn't read that. You can add your hours using the grid."

const PARSER_UNSURE = "Pablo couldn't confirm those hours. You can add them using the grid."

const GENERIC_UNSURE = "Include the days and times, or use the hours grid."

const SAVE_ERROR = "Pablo couldn't save your hours. Try again."

const PARTIAL_SAVE_ERROR = "Some of those hours could not be saved. Try again to save the rest."

/** What the preferences API returns for a practice that never saved a zone
 * (`UserPreferences.timezone` in backend/app/models/user.py). */
const SERVER_DEFAULT_TIMEZONE = "America/New_York"

interface CalendarHoursStepProps {
  /** Its place in the setup wizard's stepper, when it is shown inside it. */
  step?: number
  /** Rules were created — the host moves on (to Google, or the calendar). */
  onSaved: () => void
  /** Left without creating anything. */
  onSkip: () => void
}

export function CalendarHoursStep({ step, onSaved, onSkip }: CalendarHoursStepProps) {
  const { data: preferences } = usePreferences()
  const people = usePeopleTerm()
  const savePreferences = useSavePreferences()
  const createRule = useCreateAvailabilityRule()
  const parseRules = useParseAvailabilityRules()
  const boxRef = useRef<HTMLTextAreaElement>(null)

  const [text, setText] = useState("")
  const [proposals, setProposals] = useState<ProposedAvailabilityRule[] | null>(null)
  const [kept, setKept] = useState<boolean[]>([])
  const [unsure, setUnsure] = useState<string | null>(null)
  const [missingType, setMissingType] = useState<string | null>(null)
  const [readings, setReadings] = useState<{ question: string | null; options: ParseReading[] } | null>(
    null
  )
  const [unsureCount, setUnsureCount] = useState(0)
  const [onGrid, setOnGrid] = useState(false)
  const [fallbackReason, setFallbackReason] = useState<string | null>(null)
  const [selection, setSelection] = useState<WorkingHoursSelection>(DEFAULT_WORKING_HOURS)
  const [timezone, setTimezone] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  // After a partial save, the rules that did not land. Only these are sent
  // on the retry — the ones that did land are not created twice — and the
  // echo is frozen until they are in, because editing it now would describe
  // hours that are already half saved.
  const [unsaved, setUnsaved] = useState<CreateAvailabilityRuleRequest[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Each read is numbered so that only the latest one is shown: the box
  // stays editable while Pablo reads, and a corrected sentence sent mid-read
  // must not be overwritten by the answer to the one before it.
  const latestCheck = useRef(0)

  // The server answers with its default zone for a practice that has never
  // saved one, so a saved value equal to that default cannot be told apart
  // from nobody having chosen. For that case the browser's own zone is the
  // better first guess; any other saved value was a person's choice and is
  // kept. Either way the zone is shown, and saved only once confirmed.
  const savedTimezone = preferences?.timezone
  const fromSettings = !!savedTimezone && savedTimezone !== SERVER_DEFAULT_TIMEZONE
  const detected = fromSettings ? savedTimezone : detectBrowserTimezone()
  const chosenTimezone = timezone ?? detected
  const locked = saving || unsaved !== null

  // Typing and clicking a chip are the same path: the chip only fills the
  // box, and this is what reads it.
  const check = useCallback(async () => {
    const sentence = text.trim()
    if (!sentence) return
    const request = ++latestCheck.current
    setError(null)
    setUnsure(null)
    setMissingType(null)
    setReadings(null)
    try {
      const result = await parseRules.mutateAsync({ text: sentence })
      if (request !== latestCheck.current) return
      if (result.proposals.length === 0 && result.readings?.length === 2) {
        // Understood, two ways: a choice, not a failure, so it does not
        // count toward handing the practice the grid.
        setReadings({ question: result.could_not_parse, options: result.readings })
        return
      }
      if (
        result.proposals.length === 0 &&
        result.refusal_reason === "unknown_appointment_type" &&
        result.unknown_appointment_type
      ) {
        // Not a misunderstanding: the sentence was clear, the practice just
        // has no such type yet. Offer to add it rather than counting this
        // toward the grid fallback.
        setMissingType(result.unknown_appointment_type)
        return
      }
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
      if (request !== latestCheck.current) return
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
      // Confirming a zone the practice can see beats leaving the one the
      // browser happened to detect while they were travelling. It goes
      // first because the rules are read in it: every refresh the creates
      // below trigger then already uses the zone the hours were given in.
      if (preferences && chosenTimezone !== preferences.timezone) {
        try {
          await savePreferences.mutateAsync({ ...preferences, timezone: chosenTimezone })
        } catch {
          setError(SAVE_ERROR)
          setSaving(false)
          return
        }
      }
      // All at once rather than one after another: a week of hours is
      // several rules, and none of them depends on another.
      const results = await Promise.allSettled(rules.map((rule) => createRule.mutateAsync(rule)))
      const failed = rules.filter((_, index) => results[index].status === "rejected")
      if (failed.length === 0) {
        setUnsaved(null)
        onSaved()
        return
      }
      const partial = unsaved !== null || failed.length < rules.length
      setUnsaved(partial ? failed : null)
      setError(partial ? PARTIAL_SAVE_ERROR : SAVE_ERROR)
      setSaving(false)
    },
    [chosenTimezone, createRule, onSaved, preferences, savePreferences, saving, unsaved]
  )

  const lines = proposals ? echoLines(proposals) : []
  const keptRules = proposals
    ? proposals
        .filter((_, index) => kept[index])
        .map(proposalToCreateRequest)
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
      <Label htmlFor="hours-timezone">Time zone</Label>
      <Select value={chosenTimezone} onValueChange={setTimezone} disabled={locked}>
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
        {fromSettings ? "From your settings." : "Detected from your browser."}
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
        eyebrow={step ? `Step ${step}` : "Hours"}
        title="What are your usual hours?"
        lede="Your general working hours. You can change them later in Settings."
      />

      {onGrid ? (
        <div className="space-y-6">
          {fallbackReason ? (
            <p className="text-sm text-neutral-600" role="status">
              {fallbackReason}
            </p>
          ) : null}

          <WorkingHoursGrid value={selection} onChange={setSelection} disabled={locked} />
          {timezoneField}

          {error ? (
            <p className="text-sm text-red-600" role="alert">
              {error}
            </p>
          ) : null}

          <div className="flex items-center gap-4">
            <Button
              type="button"
              onClick={() => save(unsaved ?? workingHoursRules(selection))}
              disabled={!isCompleteSelection(selection) || saving}
            >
              Save hours
            </Button>
            {fallbackReason ? null : (
              <button
                type="button"
                onClick={() => setOnGrid(false)}
                disabled={locked}
                className="text-sm font-medium text-muted-foreground underline underline-offset-2"
              >
                Describe my hours
              </button>
            )}
            {skipLink}
          </div>
        </div>
      ) : proposals ? (
        <div className="space-y-6">
          <div className="rounded-xl border border-border bg-muted/30 p-4">
            <p className="text-sm font-medium text-neutral-900">Check these hours</p>
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
                      disabled={locked}
                      className="text-xs font-medium text-muted-foreground underline underline-offset-2"
                    >
                      {on ? "Remove" : "Put back"}
                    </button>
                  </li>
                )
              })}
            </ul>
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
              onClick={() => save(unsaved ?? keptRules)}
              disabled={keptRules.length === 0 || saving}
            >
              Save hours
            </Button>
            <button
              type="button"
              onClick={startOver}
              disabled={locked}
              className="text-sm font-medium text-muted-foreground underline underline-offset-2"
            >
              Edit description
            </button>
            <button
              type="button"
              onClick={() => {
                setSelection(selectionFromRules(proposals) ?? DEFAULT_WORKING_HOURS)
                setOnGrid(true)
              }}
              disabled={locked}
              className="text-sm font-medium text-muted-foreground underline underline-offset-2"
            >
              Use the hours grid
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="grid gap-2">
            <Label htmlFor="hours-sentence">Describe your usual hours</Label>
            <Textarea
              id="hours-sentence"
              ref={boxRef}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder={sayPeople(EXAMPLES[0], people)}
              rows={3}
            />
          </div>

          <div className="flex flex-wrap gap-2">
            {EXAMPLES.map((template) => sayPeople(template, people)).map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => fillExample(example)}
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

          {readings ? (
            <ReadingChoice
              question={readings.question}
              readings={readings.options}
              onPick={(reading) => {
                setReadings(null)
                setUnsureCount(0)
                setProposals(reading.proposals)
                setKept(reading.proposals.map(() => true))
              }}
            />
          ) : null}

          {missingType ? (
            <MissingAppointmentTypeOffer
              key={missingType}
              name={missingType}
              onCreated={() => {
                setMissingType(null)
                void check()
              }}
              onDismiss={() => setMissingType(null)}
            />
          ) : null}

          <div className="flex items-center gap-4">
            <Button type="button" onClick={check} disabled={!text.trim()}>
              Review hours
            </Button>
            <button
              type="button"
              onClick={() => setOnGrid(true)}
              className="text-sm font-medium text-muted-foreground underline underline-offset-2"
            >
              Use the hours grid
            </button>
            {skipLink}
          </div>

          {/* Its height is held while nothing is being read, so the spinner
              appearing never moves the button that was just pressed. */}
          <div className="flex h-8 items-center" data-testid="hours-reading-slot">
            {parseRules.isPending ? <PabloSpinner label="Reading your hours" size={24} /> : null}
          </div>
        </div>
      )}
    </div>
  )
}
