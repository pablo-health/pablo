// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useState } from "react"
import Link from "next/link"
import { useQueryClient } from "@tanstack/react-query"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { formatConsentDate, giverWord } from "@/components/patients/AiConsentLine"
import { useRecordAiConsent } from "@/hooks/useAiConsent"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { fetchAiConsent, fetchAiNotesConsentSetting } from "@/lib/api/aiConsent"
import { queryKeys } from "@/lib/api/queryKeys"
import type {
  AiConsentGiver,
  AiConsentRecord,
  AiNotesConsentSetting,
} from "@/types/aiConsent"

/**
 * What the client's answer about AI-assisted notes means for starting a
 * recording: go ahead, ask first (nobody has asked yet), or stop (declined).
 * Only a practice that asks its clients is ever anything but `clear`.
 *
 * Nobody has asked yet splits on where the client is. In the room, the
 * clinician may ask before recording or record anyway. Over telehealth the
 * client may be somewhere every party has to agree to a recording, so the
 * only way to record is to ask once recording starts, which puts the answer on
 * the recording (`ask_on_recording`); the server refuses anything else.
 */
export type RecordingConsent =
  | { kind: "clear" }
  | { kind: "not_asked" }
  | { kind: "ask_on_recording" }
  | { kind: "declined"; declinedOn: string }

export function recordingConsent(
  setting: AiNotesConsentSetting,
  record: AiConsentRecord,
  telehealth = false,
): RecordingConsent {
  if (!setting.ask_clients_about_ai_notes) return { kind: "clear" }
  const current = record.current
  if (!current) return telehealth ? { kind: "ask_on_recording" } : { kind: "not_asked" }
  if (current.decision === "declined") return { kind: "declined", declinedOn: current.effective_on }
  return { kind: "clear" }
}

/**
 * Read the practice setting and, when it asks, the client's answer, through
 * the same cache the chart reads. The server refuses a declined client's
 * recording regardless; this is what lets the screen say so before starting.
 */
export function useRecordingConsentCheck(): (
  patientId: string,
  telehealth?: boolean,
) => Promise<RecordingConsent> {
  const queryClient = useQueryClient()
  return useCallback(
    async (patientId: string, telehealth = false) => {
      const setting = await queryClient.fetchQuery({
        queryKey: queryKeys.aiConsent.practiceSetting(),
        queryFn: () => fetchAiNotesConsentSetting(),
        staleTime: 5 * 60 * 1000,
      })
      if (!setting.ask_clients_about_ai_notes) return { kind: "clear" }
      const record = await queryClient.fetchQuery({
        queryKey: queryKeys.aiConsent.byPatient(patientId),
        queryFn: () => fetchAiConsent(patientId),
      })
      return recordingConsent(setting, record, telehealth)
    },
    [queryClient],
  )
}

interface RecordingConsentDialogProps {
  patientId: string
  /** What to ask about; `null` or `clear` keeps the dialog closed. */
  consent: RecordingConsent | null
  onCancel: () => void
  /** Go ahead and record, after "agreed today" is saved. */
  onStart: () => void
  /** Go ahead and record with nothing on file. */
  onRecordAnyway: () => void
  /** Start recording and ask then (telehealth with nothing on file). */
  onAskNow: () => void
}

/**
 * Shown before a recording starts. A declined client: say so, with the way to
 * the chart where the answer can be changed. Nobody has asked yet: one click
 * records a verbal OK for today and starts, or the clinician records anyway.
 * Over telehealth with nothing on file: ask once recording starts, or don't
 * record.
 */
export function RecordingConsentDialog({
  patientId,
  consent,
  onCancel,
  onStart,
  onRecordAnyway,
  onAskNow,
}: RecordingConsentDialogProps) {
  const people = usePeopleTerm()
  const record = useRecordAiConsent()
  const [error, setError] = useState<string | null>(null)
  const [giver, setGiver] = useState<AiConsentGiver>("client")
  const open = consent !== null && consent.kind !== "clear"

  function close() {
    setError(null)
    setGiver("client")
    onCancel()
  }

  async function agreedToday() {
    setError(null)
    try {
      // No date: the server records the clinician's own today. Only an
      // in-person visit reaches this answer (telehealth asks on the
      // recording), so that is how it was given.
      await record.mutateAsync({
        patientId,
        data: { decision: "consented", modality: "in_person", consented_by: giver },
      })
    } catch {
      setError("Could not save. Please try again.")
      return
    }
    onStart()
  }

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : close())}>
      <DialogContent>
        {consent?.kind === "declined" ? (
          <>
            <DialogHeader>
              <DialogTitle>AI-assisted notes declined</DialogTitle>
              <DialogDescription>
                This {people.one} declined AI-assisted notes on{" "}
                {formatConsentDate(consent.declinedOn)}.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" asChild>
                <Link href={`/dashboard/patients/${patientId}`}>Open chart</Link>
              </Button>
              <Button onClick={close}>Close</Button>
            </DialogFooter>
          </>
        ) : consent?.kind === "ask_on_recording" ? (
          <>
            <DialogHeader>
              <DialogTitle>No consent on file</DialogTitle>
              <DialogDescription>
                For a telehealth session, ask once recording starts, so the {people.one}&apos;s
                answer is on the recording.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="ghost" onClick={close}>
                Don&apos;t record
              </Button>
              <Button onClick={onAskNow}>Ask now</Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>No consent on file</DialogTitle>
              <DialogDescription>
                Ask whether the {people.one} agrees to AI-assisted notes before you record.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-1.5">
              <Label htmlFor="recording-consent-giver">Answered by</Label>
              <select
                id="recording-consent-giver"
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
            {error && <p className="text-sm text-red-500">{error}</p>}
            <DialogFooter>
              <Button variant="ghost" onClick={close}>
                Cancel
              </Button>
              <Button variant="outline" onClick={onRecordAnyway} disabled={record.isPending}>
                Record anyway
              </Button>
              <Button onClick={() => void agreedToday()} disabled={record.isPending}>
                {record.isPending ? "Saving…" : `${giverWord(giver, people)} agreed today`}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
