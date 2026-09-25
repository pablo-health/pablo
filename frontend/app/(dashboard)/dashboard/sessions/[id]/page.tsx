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
import {
  useSession,
  useUpdateSessionMetadata,
  useUpdateSessionRating,
} from "@/hooks/useSessions"
import { SessionDetailHeader } from "@/components/sessions/SessionDetailHeader"
import {
  TranscriptViewer,
  type TranscriptViewerHandle,
} from "@/components/sessions/TranscriptViewer"
import { NoteViewer } from "@/components/sessions/NoteViewer"
import { QualityRating } from "@/components/sessions/QualityRating"
import {
  QualityRatingWithFeedback,
  type RatingFeedback,
} from "@/components/sessions/QualityRatingWithFeedback"
import { FinalizeButton } from "@/components/sessions/FinalizeButton"
import { ChargeCardSection } from "@/components/payments/ChargeCardSection"
import { Skeleton } from "@/components/ui/skeleton"
import { AlertCircle } from "lucide-react"
import { useUpdateNoteEdits } from "@/hooks/useNotes"
import { useNoteTypeLabel } from "@/hooks/useNoteTypes"
import type { NoteContent, SOAPNoteModel } from "@/types/sessions"
import { noteContentToJson } from "@/types/sessions"

const HIGHLIGHT_DURATION_MS = 4000

interface PageProps {
  params: Promise<{ id: string }>
}

export default function SessionDetailPage({ params }: PageProps) {
  const { id } = use(params)
  const { data: session, isLoading, error } = useSession(id, undefined, {
    // Poll while the note is being generated so the page updates itself
    // without a manual refresh.
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return s === "queued" || s === "processing" ? 3000 : false
    },
  })
  const updateRatingMutation = useUpdateSessionRating()
  const updateMetadataMutation = useUpdateSessionMetadata()

  // Local state for quality rating and feedback during review (before finalization)
  const [localRatingFeedback, setLocalRatingFeedback] = useState<RatingFeedback>({
    rating: null,
    reason: "",
    sections: [],
  })

  // Local state for edited SOAP note (before finalization)
  const [localSoapNoteEdited, setLocalSoapNoteEdited] = useState<SOAPNoteModel | null>(null)
  // Last saved edit of a non-SOAP note, shown until the session refetches.
  const [localNoteEdited, setLocalNoteEdited] = useState<NoteContent | null>(null)
  const updateNoteEdits = useUpdateNoteEdits()
  const noteTypeLabel = useNoteTypeLabel()

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

  const handleNoteSave = (edited: NoteContent) => {
    if (edited.note_type === "soap") {
      // SOAP edits are held here and persisted by finalize (soap_note_edited).
      const soap: SOAPNoteModel = {
        subjective: edited.subjective,
        objective: edited.objective,
        assessment: edited.assessment,
        plan: edited.plan,
      }
      setLocalSoapNoteEdited(soap)
      return
    }
    // Finalize has no slot for any other type's content, so those edits are
    // saved to the note straight away — the same PATCH the standalone note
    // page uses. The local copy shows the edit until the session refetches.
    const noteId = session?.note?.id
    if (!noteId) return
    setLocalNoteEdited(edited)
    updateNoteEdits.mutate(
      { noteId, data: { content_edited: noteContentToJson(edited) } },
      { onError: () => setLocalNoteEdited(null) },
    )
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

  const note = session.note
  // localSoapNoteEdited is only ever set from a SOAP save, so tagging it
  // "soap" here restates its type rather than imposing one.
  const pendingEdited: NoteContent | null = localSoapNoteEdited
    ? { note_type: "soap", ...localSoapNoteEdited }
    : localNoteEdited
  // A session with no note yet doesn't say which type it will be.
  const noteHeading = note ? `${noteTypeLabel(note.note_type)} note` : "Note"
  const canReview =
    session.status === "pending_review" && note !== null
  const finalizedRating = note?.finalized_at ? note.quality_rating : null
  // Charging follows signing: the same column, the same place the finalize
  // action was, and only once the note actually carries a signature.
  const noteIsSigned = !!note?.finalized_at

  return (
    <div className="space-y-6">
      {/* Header — above both panes */}
      <SessionDetailHeader
        patientName={session.patient_name}
        sessionDate={session.session_date}
        sessionNumber={session.session_number}
        status={session.status}
        sessionId={session.id}
        editableSessionDate={session.status === "pending_review"}
        savingSessionDate={updateMetadataMutation.isPending}
        onSessionDateChange={(value) =>
          updateMetadataMutation.mutate({
            sessionId: session.id,
            data: { session_date: value },
          })
        }
      />

      {/* Split-pane layout: side-by-side on lg+, stacked below */}
      <div
        className="lg:grid lg:grid-cols-2 lg:gap-6 space-y-6 lg:space-y-0"
        data-testid="split-pane"
      >
        {/* Left pane — the source: a recorded transcript, or the original
            document for an imported note (shown beside the parsed note so the
            clinician can verify the parse). */}
        <section
          className="lg:overflow-y-auto lg:max-h-[calc(100vh-12rem)] lg:sticky lg:top-6"
          data-testid="transcript-pane"
        >
          <h2 className="text-lg font-semibold text-neutral-900 mb-3 lg:sticky lg:top-0 lg:bg-white lg:z-10 lg:pb-2">
            {session.source === "imported" ? "Original document" : "Transcript"}
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
              <h2 className="text-lg font-semibold text-neutral-900">{noteHeading}</h2>
              {finalizedRating !== null && (
                <div className="flex items-center gap-2">
                  <span className="text-sm text-neutral-600">Quality:</span>
                  <QualityRating
                    value={finalizedRating}
                    onChange={handleFinalizedRatingChange}
                    readonly={false}
                  />
                </div>
              )}
            </div>

            {note ? (
              <NoteViewer
                note={note}
                pendingEdited={pendingEdited}
                pdfMetadata={{
                  patient_name: session.patient_name,
                  session_number: session.session_number,
                  session_date: session.session_date,
                }}
                groundingSource={
                  session.source === "imported"
                    ? session.transcript.content
                    : undefined
                }
                readonly={session.status !== "pending_review"}
                onSave={
                  session.status === "pending_review" ? handleNoteSave : undefined
                }
                onClaimClick={handleClaimClick}
              />
            ) : (
              <div className="card p-12 text-center">
                <p className="text-neutral-500">
                  {session.status === "processing"
                    ? "Note is being generated…"
                    : session.status === "failed"
                      ? "Note generation failed"
                      : "Note not available"}
                </p>
              </div>
            )}
            {updateNoteEdits.isError && (
              <p role="alert" className="mt-2 text-sm text-red-600">
                Your changes weren&apos;t saved. Try again.
              </p>
            )}
          </div>

          {/* Quality Rating & Finalize Section */}
          {canReview && (
            <div className="border-t border-neutral-200 pt-6">
              <div className="space-y-6">
                <div>
                  <h3 className="text-lg font-semibold text-neutral-900 mb-2">Review Session</h3>
                  <p className="text-sm text-neutral-600">
                    Finalize to complete. Rating the quality of this session is
                    optional.
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
                    soapNoteEdited={localSoapNoteEdited}
                  />
                </div>
              </div>
            </div>
          )}

          {noteIsSigned && (
            <div className="border-t border-neutral-200 pt-6">
              <ChargeCardSection patientId={session.patient_id} />
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
