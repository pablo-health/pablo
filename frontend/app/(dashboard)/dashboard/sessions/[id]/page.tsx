// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Session Detail Page
 *
 * Displays full session details with:
 * - Session header (patient, date, status) — above both panes
 * - Side-by-side layout on desktop (lg+): transcript left, SOAP right
 * - Stacked layout on mobile/tablet: transcript above SOAP
 * - Click-to-navigate from SOAP claims to transcript segments
 */

"use client"

import { use, useState, useRef, useCallback, useEffect } from "react"
import { useSession, useUpdateSessionRating } from "@/hooks/useSessions"
import { SessionDetailHeader } from "@/components/sessions/SessionDetailHeader"
import {
  TranscriptViewer,
  type TranscriptViewerHandle,
} from "@/components/sessions/TranscriptViewer"
import { SOAPViewer } from "@/components/sessions/SOAPViewer"
import { QualityRating } from "@/components/sessions/QualityRating"
import {
  QualityRatingWithFeedback,
  type RatingFeedback,
} from "@/components/sessions/QualityRatingWithFeedback"
import { FinalizeButton } from "@/components/sessions/FinalizeButton"
import { Skeleton } from "@/components/ui/skeleton"
import { AlertCircle } from "lucide-react"
import type { SOAPNoteModel } from "@/types/sessions"

const HIGHLIGHT_DURATION_MS = 4000

interface PageProps {
  params: Promise<{ id: string }>
}

export default function SessionDetailPage({ params }: PageProps) {
  const { id } = use(params)
  const { data: session, isLoading, error } = useSession(id)
  const updateRatingMutation = useUpdateSessionRating()

  // Local state for quality rating and feedback during review (before finalization)
  const [localRatingFeedback, setLocalRatingFeedback] = useState<RatingFeedback>({
    rating: null,
    reason: "",
    sections: [],
  })

  // Local state for edited SOAP note (before finalization)
  const [localSoapNoteEdited, setLocalSoapNoteEdited] = useState<SOAPNoteModel | null>(null)

  // Source linking state
  const [highlightedSegments, setHighlightedSegments] = useState<number[]>([])
  const transcriptRef = useRef<TranscriptViewerHandle>(null)
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Clean up highlight timer on unmount
  useEffect(() => {
    return () => {
      if (highlightTimerRef.current) {
        clearTimeout(highlightTimerRef.current)
      }
    }
  }, [])

  // Handler for clicking a sourced SOAP claim
  const handleClaimClick = useCallback((sourceSegmentIds: number[]) => {
    if (highlightTimerRef.current) {
      clearTimeout(highlightTimerRef.current)
    }

    setHighlightedSegments(sourceSegmentIds)

    if (sourceSegmentIds.length > 0 && transcriptRef.current) {
      transcriptRef.current.scrollToSegment(sourceSegmentIds[0])
    }

    highlightTimerRef.current = setTimeout(() => {
      setHighlightedSegments([])
    }, HIGHLIGHT_DURATION_MS)
  }, [])

  // Handler for updating rating on finalized sessions
  const handleFinalizedRatingChange = async (rating: number) => {
    try {
      await updateRatingMutation.mutateAsync({
        sessionId: id,
        data: { quality_rating: rating },
      })
    } catch {
      console.error("Failed to update rating")
    }
  }

  const handleReviewRatingFeedbackChange = (feedback: RatingFeedback) => {
    setLocalRatingFeedback(feedback)
  }

  const handleSOAPSave = (editedNote: SOAPNoteModel) => {
    setLocalSoapNoteEdited(editedNote)
  }

  // Loading state
  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="border-b border-neutral-200 pb-6">
          <Skeleton className="h-10 w-64 mb-2" />
          <Skeleton className="h-5 w-48" />
        </div>
        <div className="lg:grid lg:grid-cols-2 lg:gap-6 space-y-6 lg:space-y-0">
          <Skeleton className="h-96 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      </div>
    )
  }

  // Error state
  if (error) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center space-y-4">
          <AlertCircle className="h-12 w-12 text-red-500 mx-auto" />
          <h2 className="text-xl font-semibold text-neutral-900">
            Failed to load session
          </h2>
          <p className="text-neutral-600">
            {error instanceof Error ? error.message : "An error occurred"}
          </p>
        </div>
      </div>
    )
  }

  // Session not found
  if (!session) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center space-y-4">
          <AlertCircle className="h-12 w-12 text-neutral-400 mx-auto" />
          <h2 className="text-xl font-semibold text-neutral-900">Session not found</h2>
          <p className="text-neutral-600">
            The session you&apos;re looking for doesn&apos;t exist or has been deleted.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header — above both panes */}
      <SessionDetailHeader
        patientName={session.patient_name}
        sessionDate={session.session_date}
        sessionNumber={session.session_number}
        status={session.status}
        sessionId={session.id}
      />

      {/* Split-pane layout: side-by-side on lg+, stacked below */}
      <div
        className="lg:grid lg:grid-cols-2 lg:gap-6 space-y-6 lg:space-y-0"
        data-testid="split-pane"
      >
        {/* Left pane — Transcript */}
        <section
          className="lg:overflow-y-auto lg:max-h-[calc(100vh-12rem)] lg:sticky lg:top-6"
          data-testid="transcript-pane"
        >
          <h2 className="text-lg font-semibold text-neutral-900 mb-3 lg:sticky lg:top-0 lg:bg-white lg:z-10 lg:pb-2">
            Transcript
          </h2>
          <TranscriptViewer
            ref={transcriptRef}
            transcript={session.transcript}
            transcriptSegments={session.transcript_segments}
            highlightedSegments={highlightedSegments}
          />
        </section>

        {/* Right pane — SOAP Note + Review */}
        <section
          className="lg:overflow-y-auto lg:max-h-[calc(100vh-12rem)] space-y-6"
          data-testid="soap-pane"
        >
          <div>
            <div className="flex items-center justify-between mb-3 lg:sticky lg:top-0 lg:bg-white lg:z-10 lg:pb-2">
              <h2 className="text-lg font-semibold text-neutral-900">SOAP Note</h2>
              {session.status === "finalized" && session.quality_rating && (
                <div className="flex items-center gap-2">
                  <span className="text-sm text-neutral-600">Quality:</span>
                  <QualityRating
                    value={session.quality_rating}
                    onChange={handleFinalizedRatingChange}
                    readonly={false}
                  />
                </div>
              )}
            </div>

            {session.soap_note ? (
              <SOAPViewer
                sessionId={session.id}
                session={session}
                soapNote={session.soap_note}
                soapNoteEdited={localSoapNoteEdited ?? session.soap_note_edited}
                status={session.status}
                onSave={handleSOAPSave}
                onClaimClick={handleClaimClick}
              />
            ) : (
              <div className="card p-12 text-center">
                <p className="text-neutral-500">
                  {session.status === "processing"
                    ? "SOAP note is being generated..."
                    : session.status === "failed"
                      ? "SOAP note generation failed"
                      : "SOAP note not available"}
                </p>
              </div>
            )}
          </div>

          {/* Quality Rating & Finalize Section */}
          {session.status === "pending_review" && session.soap_note && (
            <div className="border-t border-neutral-200 pt-6">
              <div className="space-y-6">
                <div>
                  <h3 className="text-lg font-semibold text-neutral-900 mb-2">Review Session</h3>
                  <p className="text-sm text-neutral-600">
                    Rate the quality of this session and finalize to complete.
                  </p>
                </div>
                <QualityRatingWithFeedback
                  value={localRatingFeedback}
                  onChange={handleReviewRatingFeedbackChange}
                  readonly={false}
                />
                <div className="flex justify-end">
                  <FinalizeButton
                    sessionId={session.id}
                    status={session.status}
                    qualityRating={localRatingFeedback.rating}
                    qualityRatingReason={localRatingFeedback.reason}
                    qualityRatingSections={localRatingFeedback.sections}
                    soapNoteEdited={localSoapNoteEdited ?? session.soap_note_edited}
                  />
                </div>
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
