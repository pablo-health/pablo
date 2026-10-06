// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useAiConsent, useRecordAiConsent } from "@/hooks/useAiConsent"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import type { PeopleWords } from "@/lib/peopleTerm"
import type {
  AiConsentDecision,
  AiConsentEntry,
  AiConsentGiver,
  AiConsentModality,
} from "@/types/aiConsent"

/** A civil date (YYYY-MM-DD) as "Oct 6, 2026". Parsed as a local date so a
 * clinician west of UTC does not see the day before. */
export function formatConsentDate(isoDate: string): string {
  const [year, month, day] = isoDate.split("-").map(Number)
  return new Date(year, month - 1, day).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

/** Today on the clinician's own calendar, as YYYY-MM-DD. */
function localToday(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

const DECISION_WORD: Record<AiConsentDecision, string> = {
  consented: "agreed",
  declined: "declined",
}

export function consentLineText(current: AiConsentEntry | null): string {
  if (!current) return "AI notes: not asked yet"
  return `AI notes: ${DECISION_WORD[current.decision]} ${formatConsentDate(current.effective_on)}`
}

const MODALITY_WORD: Record<AiConsentModality, string> = {
  in_person: "In person",
  telehealth: "Telehealth",
}

/** How an answer was given, as the history shows it: "Telehealth · At home · Parent". */
export function consentDetails(entry: AiConsentEntry, people: PeopleWords): string {
  const parts: string[] = []
  if (entry.modality) parts.push(MODALITY_WORD[entry.modality])
  if (entry.client_stated_location) parts.push(entry.client_stated_location)
  if (entry.consented_by) parts.push(giverWord(entry.consented_by, people))
  return parts.join(" · ")
}

/** Who answered, as a person reads it: the people term for the client. */
export function giverWord(giver: AiConsentGiver, people: PeopleWords): string {
  return giver === "client" ? people.One : giver === "parent" ? "Parent" : "Guardian"
}

function HistoryList({ history }: { history: AiConsentEntry[] }) {
  const people = usePeopleTerm()
  if (history.length === 0) return null
  return (
    <div className="space-y-2 border-t border-neutral-200 pt-4">
      <h3 className="text-sm font-semibold text-neutral-900">History</h3>
      <ul className="space-y-1.5 text-sm text-neutral-700" data-testid="ai-consent-history">
        {[...history].reverse().map((entry) => {
          const details = consentDetails(entry, people)
          return (
            <li key={entry.id} className="flex flex-wrap justify-between gap-x-3">
              <span>
                {entry.decision === "consented" ? "Agreed" : "Declined"}{" "}
                {formatConsentDate(entry.effective_on)}
                {details && <span className="text-neutral-500"> · {details}</span>}
              </span>
              <span className="text-neutral-500">
                {entry.source === "intake_form"
                  ? "On the intake form"
                  : entry.recorded_by_name
                    ? `Recorded by ${entry.recorded_by_name}`
                    : null}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

interface AiConsentDialogProps {
  patientId: string
  history: AiConsentEntry[]
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Where the session was, when the dialog is opened from one. */
  modality?: AiConsentModality
}

/** Record a new answer and read the history. Also opened from a session note. */
export function AiConsentDialog({
  patientId,
  history,
  open,
  onOpenChange,
  modality: sessionModality,
}: AiConsentDialogProps) {
  const [decision, setDecision] = useState<AiConsentDecision | null>(null)
  const [effectiveOn, setEffectiveOn] = useState(localToday)
  const [modality, setModality] = useState<AiConsentModality | undefined>(sessionModality)
  const [location, setLocation] = useState("")
  const [giver, setGiver] = useState<AiConsentGiver>("client")
  const [error, setError] = useState<string | null>(null)
  const record = useRecordAiConsent()
  const people = usePeopleTerm()

  function handleOpenChange(next: boolean) {
    if (!next) {
      setDecision(null)
      setEffectiveOn(localToday())
      setModality(sessionModality)
      setLocation("")
      setGiver("client")
      setError(null)
    }
    onOpenChange(next)
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!decision) return
    setError(null)
    try {
      await record.mutateAsync({
        patientId,
        data: {
          decision,
          effective_on: effectiveOn || undefined,
          modality,
          client_stated_location:
            modality === "telehealth" && location.trim() ? location.trim() : undefined,
          consented_by: giver,
        },
      })
      handleOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save. Please try again.")
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>AI notes</DialogTitle>
          <DialogDescription>
            Whether the {people.one} agreed to sessions being recorded and drafted into notes. The
            answer applies to every session until you change it.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <fieldset className="space-y-2">
            <legend className="sr-only">{people.One}&apos;s answer</legend>
            {(["consented", "declined"] as const).map((value) => (
              <label key={value} className="flex items-center gap-2 text-sm text-neutral-800">
                <input
                  type="radio"
                  name="ai-consent-decision"
                  value={value}
                  checked={decision === value}
                  onChange={() => setDecision(value)}
                />
                {people.One} {value === "consented" ? "agreed" : "declined"}
              </label>
            ))}
          </fieldset>

          <div className="space-y-1.5">
            <Label htmlFor="ai-consent-date">Date</Label>
            <Input
              id="ai-consent-date"
              type="date"
              value={effectiveOn}
              max={localToday()}
              onChange={(e) => setEffectiveOn(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ai-consent-giver">Answered by</Label>
            <select
              id="ai-consent-giver"
              value={giver}
              onChange={(e) => setGiver(e.target.value as AiConsentGiver)}
              className="block w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm"
            >
              {(["client", "parent", "guardian"] as const).map((value) => (
                <option key={value} value={value}>
                  {giverWord(value, people)}
                </option>
              ))}
            </select>
          </div>

          <fieldset className="space-y-2">
            <legend className="text-sm font-medium text-neutral-900">How you met</legend>
            <div className="flex gap-4">
              {(["in_person", "telehealth"] as const).map((value) => (
                <label key={value} className="flex items-center gap-2 text-sm text-neutral-800">
                  <input
                    type="radio"
                    name="ai-consent-modality"
                    value={value}
                    checked={modality === value}
                    onChange={() => setModality(value)}
                  />
                  {MODALITY_WORD[value]}
                </label>
              ))}
            </div>
          </fieldset>

          {modality === "telehealth" && (
            <div className="space-y-1.5">
              <Label htmlFor="ai-consent-location">
                Where the {people.one} said they were
              </Label>
              <Input
                id="ai-consent-location"
                value={location}
                maxLength={200}
                onChange={(e) => setLocation(e.target.value)}
              />
            </div>
          )}

          {error && <p className="text-sm text-red-500">{error}</p>}

          <DialogFooter>
            <Button type="submit" disabled={!decision || record.isPending}>
              {record.isPending ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </form>

        <HistoryList history={history} />
      </DialogContent>
    </Dialog>
  )
}

/**
 * The client's answer about AI-assisted notes, in the chart header. Always
 * shown, whatever the practice's settings: the record exists either way, and
 * "not asked yet" is a state worth seeing. Opens a dialog to record a new
 * answer and read the history.
 */
export function AiConsentLine({ patientId }: { patientId: string }) {
  const { data } = useAiConsent(patientId)
  const [open, setOpen] = useState(false)

  // Nothing until the answer has loaded: "not asked yet" shown while a real
  // answer is still on its way would be a claim the data has not made.
  if (!data) return null

  return (
    <>
      <button
        type="button"
        data-testid="ai-consent-line"
        onClick={() => setOpen(true)}
        className="inline-flex px-3 py-1 text-sm font-medium rounded-full border border-neutral-200 text-neutral-700 hover:bg-neutral-50 transition-colors"
      >
        {consentLineText(data.current)}
      </button>
      <AiConsentDialog
        patientId={patientId}
        history={data.history}
        open={open}
        onOpenChange={setOpen}
      />
    </>
  )
}
