// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { ChatPanelWithHistory } from "@/components/chat/ChatPanelWithHistory"
import type { SourceSelection } from "@/lib/chat/types"

const DEV_DEFAULT_SELECTION: SourceSelection = {
  progress_notes_recent: { limit: 3 },
  most_recent_intake: true,
  treatment_plan_active: true,
  safety_plan_active: true,
  current_medications: true,
}

interface DevChatMountProps {
  patientId: string
  callerFeatureKey: string
  callerSystemPrompt: string
}

export function DevChatMount({
  patientId,
  callerFeatureKey,
  callerSystemPrompt,
}: DevChatMountProps) {
  if (!patientId) {
    return (
      <div
        data-slot="dev-chat-missing-patient"
        className="rounded-lg border border-dashed border-neutral-300 bg-neutral-50 p-6 text-sm text-neutral-600"
      >
        Pass <code className="font-mono">?patient_id=&lt;uuid&gt;</code> in
        the URL to mount the chat panel.
      </div>
    )
  }

  return (
    <ChatPanelWithHistory
      patientId={patientId}
      callerFeatureKey={callerFeatureKey}
      callerSystemPrompt={callerSystemPrompt}
      defaultSourceSelection={DEV_DEFAULT_SELECTION}
      title="Dev chat"
      className="h-[calc(100dvh-8rem)]"
    />
  )
}
