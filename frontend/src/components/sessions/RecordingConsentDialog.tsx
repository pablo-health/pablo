// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback } from "react"
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
import { formatConsentDate } from "@/components/patients/AiConsentLine"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { fetchAiConsent, fetchAiNotesConsentSetting } from "@/lib/api/aiConsent"
import { queryKeys } from "@/lib/api/queryKeys"
import type { AiConsentRecord, AiNotesConsentSetting } from "@/types/aiConsent"

/**
 * What the client's answer about AI-assisted notes means for starting a
 * recording: go ahead, ask once recording starts (nobody has asked yet), or
 * stop (declined). Only a practice that asks its clients is ever anything but
 * `clear`.
 *
 * With nothing on file, in the room or over telehealth, the only way to record
 * is to ask once recording starts, so the answer is on the recording
 * (`ask_on_recording`). The desktop app shows the read-aloud script only then,
 * because its first line says recording has started. The server refuses a
 * telehealth start with nothing on file unless the start says it is asking.
 */
export type RecordingConsent =
  | { kind: "clear" }
  | { kind: "ask_on_recording" }
  | { kind: "declined"; declinedOn: string }

export function recordingConsent(
  setting: AiNotesConsentSetting,
  record: AiConsentRecord,
): RecordingConsent {
  if (!setting.ask_clients_about_ai_notes) return { kind: "clear" }
  const current = record.current
  if (!current) return { kind: "ask_on_recording" }
  if (current.decision === "declined") return { kind: "declined", declinedOn: current.effective_on }
  return { kind: "clear" }
}

/**
 * Read the practice setting and, when it asks, the client's answer, through
 * the same cache the chart reads. The server refuses a declined client's
 * recording regardless; this is what lets the screen say so before starting.
 */
export function useRecordingConsentCheck(): (patientId: string) => Promise<RecordingConsent> {
  const queryClient = useQueryClient()
  return useCallback(
    async (patientId: string) => {
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
      return recordingConsent(setting, record)
    },
    [queryClient],
  )
}

interface RecordingConsentDialogProps {
  patientId: string
  /** What to ask about; `null` or `clear` keeps the dialog closed. */
  consent: RecordingConsent | null
  onCancel: () => void
  /** Start recording and ask then. */
  onAskOnRecording: () => void
}

/**
 * Shown before a recording starts. A declined client: say so, with the way to
 * the chart where the answer can be changed. Nobody has asked yet: start
 * recording and ask then, or don't record.
 */
export function RecordingConsentDialog({
  patientId,
  consent,
  onCancel,
  onAskOnRecording,
}: RecordingConsentDialogProps) {
  const people = usePeopleTerm()
  const open = consent !== null && consent.kind !== "clear"

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onCancel())}>
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
              <Button onClick={onCancel}>Close</Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>No consent for AI-assisted notes</DialogTitle>
              {/* The same words as the desktop app's sheet. The script is shown
                  there once recording is running, never before. */}
              <DialogDescription>
                You&apos;ll see what to read aloud once recording starts.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="ghost" onClick={onCancel}>
                Don&apos;t record
              </Button>
              <Button className="h-auto min-h-9 whitespace-normal" onClick={onAskOnRecording}>
                Start recording and ask
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
