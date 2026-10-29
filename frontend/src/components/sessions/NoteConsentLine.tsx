// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { AiConsentDialog, formatConsentDate } from "@/components/patients/AiConsentLine"
import { useAiConsent, useAsksClientsAboutAiNotes } from "@/hooks/useAiConsent"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import type { PeopleWords } from "@/lib/peopleTerm"
import type { AiConsentEntry, AiConsentModality } from "@/types/aiConsent"

export const NO_CONSENT_ON_FILE = "No consent on file"

export function noteConsentLineText(current: AiConsentEntry | null, people: PeopleWords): string {
  if (!current) return NO_CONSENT_ON_FILE
  const verb = current.decision === "consented" ? "agreed to" : "declined"
  return `${people.One} ${verb} AI-assisted notes on ${formatConsentDate(current.effective_on)}`
}

/**
 * The client's answer about AI-assisted notes, on a session note.
 *
 * Read from the client's consent record each time the note is shown — never
 * part of the note's content and never drafted by a model, so it cannot drift
 * from the record and an edit to the note cannot change it. It is the current
 * answer, which holds for every session until the client gives another.
 *
 * Only when the practice asks clients about AI-assisted notes; nothing at all
 * until both the setting and the answer have loaded, so the line never shows
 * a state the data has not confirmed.
 */
export function NoteConsentLine({
  patientId,
  modality,
}: {
  patientId: string
  /** Where the session was, so recording the answer starts from it. */
  modality?: AiConsentModality
}) {
  const asks = useAsksClientsAboutAiNotes()
  const { data } = useAiConsent(asks ? patientId : undefined)
  const people = usePeopleTerm()
  const [open, setOpen] = useState(false)

  if (!asks || !data) return null

  return (
    <>
      <p data-testid="note-consent-line" className="mb-3 text-sm text-neutral-600">
        <span>{noteConsentLineText(data.current, people)}</span>
        {!data.current && (
          <>
            {" · "}
            <button
              type="button"
              onClick={() => setOpen(true)}
              className="font-medium text-primary-700 hover:underline"
            >
              Record consent
            </button>
          </>
        )}
      </p>
      <AiConsentDialog
        patientId={patientId}
        history={data.history}
        open={open}
        onOpenChange={setOpen}
        modality={modality}
      />
    </>
  )
}
