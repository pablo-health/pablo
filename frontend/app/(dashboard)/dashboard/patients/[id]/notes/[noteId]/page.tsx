// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Standalone Note Detail Page (pa-0nx.4)
 *
 * Renders a single note from /api/notes/{id}. Used for notes that are
 * patient-owned without an associated recording session — created via the
 * "New note" entry on the patient detail page.
 *
 * Edits and finalize-with-quality-rating both flow through the /api/notes
 * surface, so this page does not depend on session state at all.
 */

"use client"

import { use, useState } from "react"
import Link from "next/link"
import { AlertCircle, ArrowLeft, Check } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { NoteViewer } from "@/components/sessions/NoteViewer"
import {
  QualityRatingWithFeedback,
  type RatingFeedback,
} from "@/components/sessions/QualityRatingWithFeedback"
import { usePatient } from "@/hooks/usePatients"
import {
  useFinalizeNote,
  useNote,
  useUpdateNoteEdits,
} from "@/hooks/useNotes"
import type { NoteContent } from "@/types/sessions"
import { noteContentToJson } from "@/types/sessions"

interface PageProps {
  params: Promise<{ id: string; noteId: string }>
}

export default function StandaloneNotePage({ params }: PageProps) {
  const { id: patientId, noteId } = use(params)
  const { data: patient } = usePatient(patientId)
  const { data: note, isLoading, error } = useNote(noteId)
  const updateEdits = useUpdateNoteEdits()
  const finalize = useFinalizeNote()

  const [feedback, setFeedback] = useState<RatingFeedback>({
    rating: null,
    reason: "",
    sections: [],
  })

  const handleSave = async (edited: NoteContent) => {
    if (!note) return
    await updateEdits.mutateAsync({
      noteId: note.id,
      data: { content_edited: noteContentToJson(edited) },
    })
  }

  const handleFinalize = async () => {
    if (!note || feedback.rating === null) return
    await finalize.mutateAsync({
      noteId: note.id,
      data: {
        quality_rating: feedback.rating,
        ...(feedback.reason && { quality_rating_reason: feedback.reason }),
        ...(feedback.sections.length > 0 && {
          quality_rating_sections: feedback.sections,
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
  const patientName = patient
    ? `${patient.first_name} ${patient.last_name}`
    : "Patient"

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
          {note.note_type} note
        </h1>
        <p className="text-neutral-600">
          {patientName}
          {isFinalized && note.finalized_at && (
            <span className="text-sm text-neutral-500 ml-2">
              · Finalized {new Date(note.finalized_at).toLocaleDateString()}
            </span>
          )}
        </p>
      </div>

      <NoteViewer
        note={note}
        readonly={isFinalized}
        pdfMetadata={{
          patient_name: patientName,
          session_date: note.created_at,
        }}
        onSave={isFinalized ? undefined : handleSave}
      />

      {!isFinalized && (
        <div className="card space-y-6">
          <div>
            <h3 className="text-lg font-semibold text-neutral-900 mb-2">
              Finalize note
            </h3>
            <p className="text-sm text-neutral-600">
              Once finalized, the note becomes read-only and is recorded in the
              audit log.
            </p>
          </div>
          <QualityRatingWithFeedback
            value={feedback}
            onChange={setFeedback}
            readonly={false}
          />
          <div className="flex justify-end">
            <Button
              size="lg"
              onClick={handleFinalize}
              disabled={feedback.rating === null || finalize.isPending}
              className="bg-secondary-600 hover:bg-secondary-700 text-white"
            >
              <Check className="mr-2 h-4 w-4" />
              {finalize.isPending ? "Finalizing…" : "Finalize note"}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
