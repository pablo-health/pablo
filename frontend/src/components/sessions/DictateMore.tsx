// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * "Dictate more": add to a session's note by voice after the recording has
 * stopped. The clip goes to the server, which transcribes it and either
 * redrafts the unsigned note with it or, for a signed note, drafts an
 * addendum to review and sign (see `SessionDictationService`). Dictation is
 * documentation time; nothing here touches the session's own recording.
 *
 * Before a redraft of an edited note, the clinician chooses what happens to
 * their edits, with keeping them the default.
 */

"use client"

import { useState } from "react"
import { Mic, Square, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useSessionDictations } from "@/hooks/useDictations"
import {
  useDictationRecorder,
  type DictationClip,
  type RecorderProblem,
} from "@/hooks/useDictationRecorder"
import type { RedraftEdits } from "@/types/notes"
import { RedraftChoiceDialog } from "./RedraftChoiceDialog"

export interface DictateMoreProps {
  sessionId: string
  /** A signed note gets a draft addendum instead of a redraft. */
  signed: boolean
  /** The clinician has edited the (unsigned) note. */
  hasEdits: boolean
  /** Nothing may be added right now (the note is being redrafted). */
  disabled?: boolean
  onSend: (clip: DictationClip, edits?: RedraftEdits) => Promise<{ id: string }>
}

const PROBLEMS: Record<RecorderProblem, string> = {
  denied:
    "Pablo can't use your microphone. Allow it for this site in your browser's settings, then try again.",
  "no-microphone": "No microphone was found. Connect one and try again.",
  unsupported: "This browser can't record audio. Try a current version of Chrome, Edge, Firefox or Safari.",
  failed: "Recording didn't start. Try again.",
}

const clock = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`

export function DictateMore({ sessionId, signed, hasEdits, disabled, onSend }: DictateMoreProps) {
  const recorder = useDictationRecorder()
  const { data: dictations } = useSessionDictations(sessionId)
  const [sending, setSending] = useState(false)
  const [sendFailed, setSendFailed] = useState(false)
  const [sentId, setSentId] = useState<string | null>(null)
  const [choosing, setChoosing] = useState(false)

  const sent = dictations?.data.find((d) => d.id === sentId)

  const send = async (edits?: RedraftEdits) => {
    if (!recorder.clip) return
    setChoosing(false)
    setSending(true)
    setSendFailed(false)
    try {
      const dictation = await onSend(recorder.clip, edits)
      setSentId(dictation.id)
      recorder.discard()
    } catch {
      setSendFailed(true)
    } finally {
      setSending(false)
    }
  }

  return (
    <section aria-label="Dictate more" className="card space-y-3 p-4">
      {recorder.state === "recording" ? (
        <div className="flex flex-wrap items-center gap-3">
          <span role="status" className="flex items-center gap-2 text-sm text-neutral-800">
            <span className="h-2 w-2 animate-pulse rounded-full bg-red-600" aria-hidden />
            Recording {clock(recorder.seconds)}
          </span>
          <Button size="sm" onClick={recorder.stop}>
            <Square className="mr-2 h-4 w-4" />
            Stop
          </Button>
          <Button size="sm" variant="outline" onClick={recorder.discard}>
            <Trash2 className="mr-2 h-4 w-4" />
            Discard
          </Button>
        </div>
      ) : recorder.state === "recorded" && recorder.clip ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-neutral-800">Dictation, {clock(recorder.clip.seconds)}</span>
          <Button
            size="sm"
            onClick={() => (!signed && hasEdits ? setChoosing(true) : send())}
            disabled={sending || disabled}
          >
            {sending ? "Sending…" : signed ? "Draft an addendum" : "Add to note"}
          </Button>
          <Button size="sm" variant="outline" onClick={recorder.discard} disabled={sending}>
            <Trash2 className="mr-2 h-4 w-4" />
            Discard
          </Button>
        </div>
      ) : (
        <Button
          variant="outline"
          onClick={recorder.start}
          disabled={disabled || recorder.state === "starting" || sent?.status === "transcribing"}
        >
          <Mic className="mr-2 h-4 w-4" />
          Dictate more
        </Button>
      )}

      {recorder.problem && (
        <p role="alert" className="text-sm text-red-700">
          {PROBLEMS[recorder.problem]}
        </p>
      )}
      {sendFailed && (
        <p role="alert" className="text-sm text-red-700">
          The dictation didn&apos;t send. Try again.
        </p>
      )}
      {sent?.status === "transcribing" && (
        <p role="status" className="text-sm text-neutral-700">
          Transcribing your dictation…
        </p>
      )}
      {sent?.status === "failed" && (
        <p role="alert" className="text-sm text-red-700">
          That dictation couldn&apos;t be transcribed, so the note hasn&apos;t changed. Try again.
        </p>
      )}

      <RedraftChoiceDialog
        open={choosing}
        onOpenChange={setChoosing}
        onConfirm={send}
        keepDescription="Fields you changed stay as you wrote them. The dictation goes into the rest."
        confirmLabel="Add to note"
        pending={sending}
      />
    </section>
  )
}
