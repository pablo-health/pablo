// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { ThreadView } from "@/components/messages/ThreadView"
import { usePortalSettings } from "@/hooks/usePortalSettings"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import type { ReplyInboxOutcome } from "@/lib/api/messageInbox"
import { EarlierMessagesPrompt } from "./EarlierMessagesPrompt"
import type { InboxItemRendererProps } from "./itemRenderers"
import { repliesOffNote } from "./portalOff"

/**
 * One client message, shown inside the conversation around it. A reply
 * answers this message, so it resolves this one; what becomes of the client's
 * earlier unanswered messages is the prompt's to settle.
 */
export function PortalMessageItem({ item, onClose }: InboxItemRendererProps) {
  const { data: portal } = usePortalSettings()
  const people = usePeopleTerm()
  // Counted, so a second reply asks afresh rather than inheriting the first answer.
  const [reply, setReply] = useState<{ outcome: ReplyInboxOutcome; count: number } | null>(null)
  const patientName = item.patient_name ?? `This ${people.one}`

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1">
        <ThreadView
          threadId={item.context.thread_id}
          patientId={item.patient_id ?? ""}
          patientName={patientName}
          onBack={onClose}
          repliesOffNote={repliesOffNote(portal, people)}
          highlightMessageId={item.source_id}
          inReplyToMessageId={item.source_id}
          onReplied={(outcome) =>
            setReply((previous) => (outcome ? { outcome, count: (previous?.count ?? 0) + 1 } : null))
          }
        />
      </div>
      {reply && (
        <EarlierMessagesPrompt
          key={reply.count}
          messageId={item.source_id}
          patientName={patientName}
          outcome={reply.outcome}
        />
      )}
    </div>
  )
}
