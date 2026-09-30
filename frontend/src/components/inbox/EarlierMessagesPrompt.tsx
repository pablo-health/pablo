// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What happens to a client's earlier unanswered messages after a reply.
 *
 * Replying resolves the message replied to and nothing else, because older
 * messages disappearing on their own is what clinicians dislike. So, where the
 * client has earlier open messages, this asks once. "Always" and "Don't ask
 * again" are remembered as the clinician's preference (changeable in
 * Settings → Inbox); ignoring the question leaves them open and it is asked
 * again next time. When "always" handled them already, a line says how many
 * and offers Undo, so a remembered choice never makes a message vanish
 * without a trace.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { useHandleEarlierMessages, useRestoreInboxItems } from "@/hooks/useInbox"
import { usePreferences, useSavePreferences } from "@/hooks/usePreferences"
import type { ReplyInboxOutcome } from "@/lib/api/messageInbox"
import type { UserPreferences } from "@/lib/api/users"

interface EarlierMessagesPromptProps {
  /** The client message that was replied to. */
  messageId: string
  patientName: string
  outcome: ReplyInboxOutcome
}

export function EarlierMessagesPrompt({ messageId, patientName, outcome }: EarlierMessagesPromptProps) {
  const [handled, setHandled] = useState<string[]>(outcome.earlier_handled_ids)
  const [answered, setAnswered] = useState(false)
  const [undone, setUndone] = useState(false)
  const handleEarlier = useHandleEarlierMessages()
  const restore = useRestoreInboxItems()
  const { data: preferences } = usePreferences()
  const savePreferences = useSavePreferences()

  function remember(choice: UserPreferences["inbox_reply_earlier_messages"]) {
    if (preferences) savePreferences.mutate({ ...preferences, inbox_reply_earlier_messages: choice })
  }

  function markHandled() {
    setAnswered(true)
    handleEarlier.mutate(messageId, { onSuccess: (result) => setHandled(result.handled_ids) })
  }

  if (handled.length > 0 && !undone) {
    return (
      <div className="flex flex-wrap items-center gap-2 border-t border-neutral-200 px-4 py-3 text-sm" data-testid="inbox-earlier-handled" role="status">
        <p className="text-neutral-700">
          {handled.length === 1
            ? "1 earlier message marked handled"
            : `${handled.length} earlier messages marked handled`}
        </p>
        <span aria-hidden="true" className="text-neutral-400">
          ·
        </span>
        <button
          type="button"
          className="font-medium text-primary-700 underline disabled:opacity-50"
          disabled={restore.isPending}
          onClick={() =>
            restore.mutate(
              handled.map((sourceId) => ({ kind: "portal_message", sourceId })),
              { onSuccess: () => setUndone(true) },
            )
          }
        >
          Undo
        </button>
      </div>
    )
  }

  const open = outcome.earlier_open_ids.length
  if (open === 0 || answered) return null

  return (
    <div className="space-y-2 border-t border-neutral-200 px-4 py-3" data-testid="inbox-earlier-prompt" role="status">
      <p className="text-sm text-neutral-800">
        {open === 1
          ? `Also mark ${patientName}'s earlier message handled?`
          : `Also mark ${patientName}'s ${open} earlier messages handled?`}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" onClick={markHandled}>
          Yes
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => {
            remember("always")
            markHandled()
          }}
        >
          Always
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => {
            remember("never")
            setAnswered(true)
          }}
        >
          Don&apos;t ask again
        </Button>
      </div>
    </div>
  )
}
