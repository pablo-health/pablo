// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * FinalizeButton Component
 *
 * Button to finalize a session after review.
 * Uses the useFinalizeSession hook to transition from "pending_review" to "finalized".
 *
 * Features:
 * - Disabled when not in "pending_review" status
 * - Shows loading spinner during mutation
 * - Quality rating is optional; finalizing is never gated on a rating
 * - Can include edited SOAP note
 */

"use client"

import { Check } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import { useFinalizeSession } from "@/hooks/useSessions"
import type { SessionStatus, SOAPNoteModel } from "@/types/sessions"

export interface FinalizeButtonProps {
  sessionId: string
  status: SessionStatus
  qualityRating: number | null
  qualityRatingReason?: string
  qualityRatingSections?: string[]
  soapNoteEdited?: SOAPNoteModel | null
  onSuccess?: () => void
}

export function FinalizeButton({
  sessionId,
  status,
  qualityRating,
  qualityRatingReason,
  qualityRatingSections,
  soapNoteEdited,
  onSuccess,
}: FinalizeButtonProps) {
  const finalizeMutation = useFinalizeSession()
  const { readOnly } = useReadOnlyMode()

  const isDisabled =
    status !== "pending_review" || finalizeMutation.isPending

  const handleFinalize = async () => {
    try {
      await finalizeMutation.mutateAsync({
        sessionId,
        data: {
          ...(qualityRating !== null && { quality_rating: qualityRating }),
          ...(qualityRatingReason && { quality_rating_reason: qualityRatingReason }),
          ...(qualityRatingSections &&
            qualityRatingSections.length > 0 && {
              quality_rating_sections: qualityRatingSections,
            }),
          ...(soapNoteEdited && { soap_note_edited: soapNoteEdited }),
        },
      })
      onSuccess?.()
    } catch {
      // Error handling is done by React Query and can be shown via toast/notification
      console.error("Failed to finalize session")
    }
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
    <Button
      onClick={handleFinalize}
      disabled={isDisabled}
      size="lg"
      className="bg-secondary-600 hover:bg-secondary-700 text-white"
    >
      {finalizeMutation.isPending ? (
        <>
          <span className="mr-2 h-4 w-4 animate-spin">⏳</span>
          Finalizing...
        </>
      ) : (
        <>
          <Check className="mr-2 h-4 w-4" />
          Finalize Session
        </>
      )}
    </Button>
  )
}
