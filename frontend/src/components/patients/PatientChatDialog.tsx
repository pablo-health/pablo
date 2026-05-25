// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { MessageSquare } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { ChatPanelWithHistory } from "@/components/chat/ChatPanelWithHistory"
import type { SourceSelection } from "@/lib/chat/types"

interface PatientChatDialogProps {
  patientId: string
}

// Neutral OSS default context — recent progress notes plus the standing
// clinical documents. No proprietary tuning; SaaS resolves its own prompt
// server-side (we pass no callerSystemPrompt).
const DEFAULT_SELECTION: SourceSelection = {
  progress_notes_recent: { limit: 3 },
  most_recent_intake: true,
  treatment_plan_active: true,
  safety_plan_active: true,
  current_medications: true,
}

export function PatientChatDialog({ patientId }: PatientChatDialogProps) {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <MessageSquare className="h-4 w-4" />
          Chat
        </Button>
      </DialogTrigger>
      <DialogContent className="flex h-[85vh] w-[90vw] max-w-none flex-col gap-0 p-0 sm:max-w-none">
        <DialogHeader className="border-b border-neutral-200 px-6 py-4 text-left">
          <DialogTitle className="font-display text-lg font-bold text-neutral-900">
            Chat
          </DialogTitle>
          <DialogDescription className="text-sm text-neutral-500">
            Ask about this patient&apos;s chart. Responses draw only on the
            sources you select.
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 p-4">
          <ChatPanelWithHistory
            patientId={patientId}
            callerFeatureKey="patient_chat"
            callerSystemPrompt=""
            defaultSourceSelection={DEFAULT_SELECTION}
            className="h-full"
          />
        </div>
      </DialogContent>
    </Dialog>
  )
}
