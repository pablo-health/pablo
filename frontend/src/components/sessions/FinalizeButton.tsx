// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * FinalizeButton Component
 *
 * Signs and locks a session's note after review. Opens the signing dialog
 * (name and credentials, prefilled and editable, with a live preview), then
 * uses the useFinalizeSession hook to sign the note and move the session
 * from "pending_review" to "finalized" in one request.
 *
 * Features:
 * - Disabled when not in "pending_review" status
 * - Quality rating is optional; signing is never gated on a rating
 *
 * Edits don't ride along: the session page saves them to the note as they
 * are made, so signing locks what the note already holds.
 */

"use client"

import { useState } from "react"
import { Check, Lock } from "lucide-react"
import { Button } from "@/components/ui/button"
import { SignNoteDialog } from "@/components/notes/signing/SigningDialogs"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import { useFinalizeSession } from "@/hooks/useSessions"
import type { NoteSignerFields } from "@/types/notes"
import type { SessionStatus } from "@/types/sessions"

export interface FinalizeButtonProps {
  sessionId: string
  status: SessionStatus
  qualityRating: number | null
  qualityRatingReason?: string
  qualityRatingSections?: string[]
  onSuccess?: () => void
}

export function FinalizeButton({
  sessionId,
  status,
  qualityRating,
  qualityRatingReason,
  qualityRatingSections,
  onSuccess,
}: FinalizeButtonProps) {
  const finalizeMutation = useFinalizeSession()
  const { readOnly } = useReadOnlyMode()
  const [dialogOpen, setDialogOpen] = useState(false)

  const isDisabled =
    status !== "pending_review" || finalizeMutation.isPending

  // Errors propagate so the dialog stays open and says what went wrong.
  const handleSign = async (signature: NoteSignerFields) => {
    await finalizeMutation.mutateAsync({
      sessionId,
      data: {
        ...(qualityRating !== null && { quality_rating: qualityRating }),
        ...(qualityRatingReason && { quality_rating_reason: qualityRatingReason }),
        ...(qualityRatingSections &&
          qualityRatingSections.length > 0 && {
            quality_rating_sections: qualityRatingSections,
          }),
        signature,
      },
    })
    onSuccess?.()
  }

  if (status === "finalized") {
    return (
      <Button disabled variant="outline" size="lg">
        <Check className="mr-2 h-4 w-4" />
        Finalized
      </Button>
    )
  }

  // The "Finalized" badge above is a status indicator and stays; this is the
  // action itself, so it goes.
  if (readOnly) return null

  return (
    <>
      <Button
        onClick={() => setDialogOpen(true)}
        disabled={isDisabled}
        size="lg"
        className="bg-secondary-600 hover:bg-secondary-700 text-white"
      >
        <Lock className="mr-2 h-4 w-4" />
        Sign and lock
      </Button>
      <SignNoteDialog open={dialogOpen} onOpenChange={setDialogOpen} onSign={handleSign} />
    </>
  )
}
