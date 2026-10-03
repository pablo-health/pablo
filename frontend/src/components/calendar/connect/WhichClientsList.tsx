// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Checkbox } from "@/components/ui/checkbox"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import type { ImportPatientChoice, SeriesMatch } from "@/lib/api/scheduling"
import type { PeopleWords } from "@/lib/peopleTerm"

/** Whether a match is a client of the practice that someone else sees. */
export function seenElsewhere(match: SeriesMatch): boolean {
  return match.seen_by != null
}

/** One thing on a calendar that may be a client. */
export interface ClientQuestionRow {
  key: string
  /** The calendar's own wording for it. */
  title: string
  /** When it happens, in a line. */
  detail: string
  /** Right-hand note, such as how many are ahead. */
  aside?: string
  match: SeriesMatch
  /** The preselected client's chart is inactive or on hold. Confirming the
   * session offers to make them active again. */
  clientInactive?: boolean
}

const NEW_CLIENT = "new"

/** The preselected client's name, for the offer to make them active again. */
function clientNamed(row: ClientQuestionRow, people: PeopleWords): string {
  const chosen = row.match.possible.find((c) => c.patient_id === row.match.suggested_patient_id)
  return chosen?.display_name ?? `this ${people.one}`
}

function choiceLabel(choice: ImportPatientChoice): string {
  if (!choice.date_of_birth) return choice.display_name
  const [year, month, day] = choice.date_of_birth.split("-")
  return `${choice.display_name}, born ${Number(month)}/${Number(day)}/${year}`
}

/** A colleague's client: who sees them, and who to ask. Nothing about the
 * chart itself — the row's title is the calendar's own wording. */
function SeenElsewhere({ names }: { names: string[] }) {
  const people = usePeopleTerm()
  const seenBy =
    names.length > 0
      ? `, seen by ${new Intl.ListFormat("en", { type: "conjunction" }).format(names)}`
      : ""
  const ask = new Intl.ListFormat("en", { type: "disjunction" }).format([
    ...names,
    "your practice owner",
  ])
  return (
    <>
      <span className="block text-xs text-secondary-700">
        Already a {people.one} of the practice{seenBy}.
      </span>
      <span className="block text-xs text-muted-foreground">Ask {ask} for access.</span>
    </>
  )
}

/** Which client a row is: named when certain, a small choice when a few
 * clients could be it, and a new client otherwise. */
function ClientChoice({
  row,
  patientId,
  onChoose,
}: {
  row: ClientQuestionRow
  patientId: string | null
  onChoose: (patientId: string | null) => void
}) {
  const { patient, possible } = row.match
  const people = usePeopleTerm()
  if (patient) {
    return (
      <span className="block text-xs text-secondary-700">Matches {patient.display_name}</span>
    )
  }
  if (possible.length === 0) {
    return <span className="block text-xs text-muted-foreground">New {people.one}</span>
  }
  return (
    <select
      aria-label={`Which ${people.one} is ${row.title}?`}
      value={patientId ?? NEW_CLIENT}
      onChange={(event) =>
        onChoose(event.target.value === NEW_CLIENT ? null : event.target.value)
      }
      className="mt-1 rounded-md border border-border bg-card px-1.5 py-0.5 text-xs text-neutral-900"
    >
      {possible.map((choice) => (
        <option key={choice.patient_id} value={choice.patient_id}>
          {choiceLabel(choice)}
        </option>
      ))}
      <option value={NEW_CLIENT}>New {people.one}</option>
    </select>
  )
}

interface WhichClientsListProps {
  rows: ClientQuestionRow[]
  checked: Record<string, boolean>
  onToggle: (key: string) => void
  /** The existing client each row is; null for a new client. */
  clientFor: Record<string, string | null>
  onChooseClient: (key: string, patientId: string | null) => void
  /** Rows marked as not a client; remembered when saved. */
  notClient: Record<string, boolean>
  onToggleNotClient: (key: string) => void
  /** Rows whose inactive client is made active again on save. Default on. */
  reactivate?: Record<string, boolean>
  onToggleReactivate?: (key: string) => void
}

/**
 * The "which of these are clients?" list. Shared by the calendar import and
 * by the review of sessions brought in from the clinician's own calendar, so
 * both ask the question the same way.
 */
export function WhichClientsList({
  rows,
  checked,
  onToggle,
  clientFor,
  onChooseClient,
  notClient,
  onToggleNotClient,
  reactivate = {},
  onToggleReactivate,
}: WhichClientsListProps) {
  const people = usePeopleTerm()
  return (
    <div className="flex flex-col">
      {rows.map((row) => {
        const key = row.key
        const isNotClient = notClient[key] ?? false
        // A colleague's client can't be added here, so it can't be ticked.
        const elsewhere = seenElsewhere(row.match)
        // The offer to reactivate goes with the inactive chart: picking
        // another client, or a new one, takes it away.
        const offerReactivate =
          Boolean(row.clientInactive) &&
          row.match.suggested_patient_id != null &&
          (clientFor[key] ?? null) === row.match.suggested_patient_id
        return (
          // A div, not a label: the client choice sits in the row, and a
          // label would turn every click on it into a tick or an untick.
          <div
            key={key}
            className="grid grid-cols-[20px_1fr_auto] items-center gap-3 border-b border-border py-2.5 last:border-b-0"
          >
            <Checkbox
              id={`series-${key}`}
              checked={!isNotClient && !elsewhere && (checked[key] ?? false)}
              disabled={isNotClient || elsewhere}
              onCheckedChange={() => onToggle(key)}
              aria-label={row.title}
            />
            <span>
              <label htmlFor={`series-${key}`} className="block cursor-pointer">
                <span
                  className={`block text-sm font-medium ${isNotClient ? "text-muted-foreground" : "text-neutral-900"}`}
                >
                  {row.title}
                </span>
                <span className="block text-xs tabular-nums text-muted-foreground">
                  {row.detail}
                </span>
              </label>
              {elsewhere ? (
                <SeenElsewhere names={row.match.seen_by ?? []} />
              ) : isNotClient ? (
                // Leaving a row unticked only skips it for now; this answer
                // is kept, so it is not asked about again.
                <span className="block text-xs text-muted-foreground">
                  Not a {people.one}. Pablo will remember.{" "}
                  <button
                    type="button"
                    onClick={() => onToggleNotClient(key)}
                    className="font-medium underline underline-offset-2 hover:text-neutral-700"
                  >
                    Undo
                  </button>
                </span>
              ) : (
                <>
                  <ClientChoice
                    row={row}
                    patientId={clientFor[key] ?? null}
                    onChoose={(patientId) => onChooseClient(key, patientId)}
                  />
                  {offerReactivate ? (
                    <label className="mt-1 flex cursor-pointer items-center gap-1.5 text-xs text-neutral-700">
                      <Checkbox
                        checked={reactivate[key] ?? true}
                        onCheckedChange={() => onToggleReactivate?.(key)}
                        aria-label={`Make ${clientNamed(row, people)} active again`}
                      />
                      Make {clientNamed(row, people)} active again
                    </label>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => onToggleNotClient(key)}
                    className="mt-0.5 block text-xs text-muted-foreground underline underline-offset-2 hover:text-neutral-700"
                  >
                    Not a {people.one}
                  </button>
                </>
              )}
            </span>
            <span className="whitespace-nowrap text-xs tabular-nums text-muted-foreground">
              {row.aside}
            </span>
          </div>
        )
      })}
    </div>
  )
}
