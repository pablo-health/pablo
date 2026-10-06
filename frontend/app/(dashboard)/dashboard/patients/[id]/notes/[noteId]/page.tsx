// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Standalone Note Detail Page (pa-0nx.4)
 *
 * Renders a single note from /api/notes/{id}. Used for notes that are
 * patient-owned without an associated recording session — created via the
 * "New note" entry on the patient detail page.
 *
 * Edits and sign-and-lock (with the draft's quality rating) both flow
 * through the /api/notes surface, so this page does not depend on session
 * state at all. Once signed, the signature panel under the note owns
 * addenda and unlocking.
 */

"use client"

import { use, useState } from "react"
import Link from "next/link"
import { AlertCircle, ArrowLeft, Lock } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { NoteViewer } from "@/components/sessions/NoteViewer"
import { NoteConsentLine } from "@/components/sessions/NoteConsentLine"
import { OnlyYouBadge } from "@/components/notes/OnlyYouBadge"
import { NoteSignaturePanel } from "@/components/notes/signing/NoteSignaturePanel"
import { SignNoteDialog } from "@/components/notes/signing/SigningDialogs"
import {
  QualityRatingWithFeedback,
  type RatingFeedback,
} from "@/components/sessions/QualityRatingWithFeedback"
import { usePatient } from "@/hooks/usePatients"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { useNoteTypeLabel } from "@/hooks/useNoteTypes"
import { useNote, useUpdateNoteEdits } from "@/hooks/useNotes"
import { useNoteSigning, useSignNote } from "@/hooks/useNoteSigning"
import { useUserTimeZone } from "@/hooks/usePreferences"
import { pdfSignatureBlock } from "@/lib/utils/signatureBlock"
import type { NoteSignerFields } from "@/types/notes"
import type { NoteContent } from "@/types/sessions"
import { noteContentToJson } from "@/types/sessions"

interface PageProps {
  params: Promise<{ id: string; noteId: string }>
}

export default function StandaloneNotePage({ params }: PageProps) {
  const { id: patientId, noteId } = use(params)
  const { data: patient } = usePatient(patientId)
  const { data: note, isLoading, error } = useNote(noteId, undefined, {
    // Poll while the dictated note is generating so the page updates
    // itself without a manual refresh.
    refetchInterval: (query) =>
      query.state.data?.status === "processing" ? 3000 : false,
  })
  const updateEdits = useUpdateNoteEdits()
  const noteTypeLabel = useNoteTypeLabel()
  const sign = useSignNote()
  const { data: signing } = useNoteSigning(note?.id)
  const timeZone = useUserTimeZone()
  const people = usePeopleTerm()
  const [signOpen, setSignOpen] = useState(false)

  const [feedback, setFeedback] = useState<RatingFeedback>({
    rating: null,
    reason: "",
    sections: [],
  })

  // Manually-authored notes (no recording → no AI draft) have nothing
  // for the clinician to rate; the quality wizard only makes sense for
  // notes derived from a session transcript. Detect by session_id.
  const isManual = !!note && note.session_id === null
  // A drafted note is rated when it is first signed; signing it again after
  // an unlock keeps that rating.
  const needsRating = !!note && !isManual && note.quality_rating === null

  const handleSave = async (edited: NoteContent) => {
    if (!note) return
    await updateEdits.mutateAsync({
      noteId: note.id,
      data: { content_edited: noteContentToJson(edited) },
    })
  }

  const handleSign = async (signer: NoteSignerFields) => {
    if (!note) return
    await sign.mutateAsync({
      noteId: note.id,
      data: {
        ...signer,
        ...(isManual
          ? {}
          : {
              quality_rating: feedback.rating ?? undefined,
              ...(feedback.reason && { quality_rating_reason: feedback.reason }),
              ...(feedback.sections.length > 0 && {
                quality_rating_sections: feedback.sections,
              }),
            }),
      },
    })
  }

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-96 w-full" />
      </div>
    )
  }

  if (error || !note) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center space-y-4">
          <AlertCircle className="h-12 w-12 text-neutral-400 mx-auto" />
          <h2 className="text-xl font-semibold text-neutral-900">
            Note not found
          </h2>
          <p className="text-neutral-600">
            {error instanceof Error
              ? error.message
              : "This note doesn't exist or you don't have access."}
          </p>
        </div>
      </div>
    )
  }

  const isFinalized = !!note.finalized_at
  const isGenerating = note.status === "processing"
  // A session note whose redraft failed is marked failed too, but keeps its
  // content; only a note that never got any has nothing to show.
  const generationFailed = note.status === "failed" && !note.content
  const patientName = patient
    ? `${patient.first_name} ${patient.last_name}`
    : people.One

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <Link
          href={`/dashboard/patients/${patientId}/notes`}
          className="flex items-center gap-2 text-neutral-600 hover:text-neutral-900 transition-colors"
        >
          <ArrowLeft className="w-5 h-5" />
          <span>Back to notes</span>
        </Link>
      </div>

      <div>
        <h1 className="text-3xl font-display font-bold text-neutral-900 mb-1 capitalize">
          {noteTypeLabel(note.note_type)} note
          {note.restricted && (
            <span className="ml-3 align-middle normal-case">
              <OnlyYouBadge />
            </span>
          )}
        </h1>
        <p className="text-neutral-600">{patientName}</p>
      </div>

      {/* A note drafted from a recorded session carries the client's answer
          about AI-assisted notes; a note written by hand has no recording. */}
      {!isManual && <NoteConsentLine patientId={patientId} />}

      {isGenerating ? (
        <div className="card p-12 text-center">
          <p className="text-neutral-500">Note is being generated…</p>
        </div>
      ) : generationFailed ? (
        <div className="card p-12 text-center">
          <p className="text-neutral-500">
            Note generation failed. Create the note again to retry.
          </p>
        </div>
      ) : (
        <NoteViewer
          note={note}
          readonly={isFinalized}
          pdfMetadata={{
            patient_name: patientName,
            session_date: note.created_at,
            signature: pdfSignatureBlock(signing, timeZone),
          }}
          onSave={isFinalized ? undefined : handleSave}
        />
      )}

      {!isGenerating && !generationFailed && <NoteSignaturePanel note={note} />}

      {!isFinalized && !isGenerating && !generationFailed && (
        <div className="card space-y-6">
          <div>
            <h3 className="text-lg font-semibold text-neutral-900 mb-2">
              Sign and lock
            </h3>
            <p className="text-sm text-neutral-600">Signing locks the note.</p>
          </div>
          {needsRating && (
            <QualityRatingWithFeedback
              value={feedback}
              onChange={setFeedback}
              readonly={false}
            />
          )}
          <div className="flex justify-end">
            <Button
              size="lg"
              onClick={() => setSignOpen(true)}
              disabled={(needsRating && feedback.rating === null) || sign.isPending}
              className="bg-secondary-600 hover:bg-secondary-700 text-white"
            >
              <Lock className="mr-2 h-4 w-4" />
              Sign and lock
            </Button>
          </div>
          <SignNoteDialog open={signOpen} onOpenChange={setSignOpen} onSign={handleSign} />
        </div>
      )}
    </div>
  )
}
