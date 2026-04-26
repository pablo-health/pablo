// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient Notes List Page (pa-0nx.4)
 *
 * All notes for a patient — session-bound and standalone — sorted by
 * finalized_at (or created_at) descending. Source of truth is
 * ``GET /api/patients/{id}/notes``.
 */

"use client"

import { use } from "react"
import Link from "next/link"
import { ArrowLeft, FileText } from "lucide-react"
import { usePatient } from "@/hooks/usePatients"
import { usePatientNotes } from "@/hooks/useNotes"
import { Skeleton } from "@/components/ui/skeleton"
import { NewNoteButton } from "@/components/notes/NewNoteButton"
import type { Note } from "@/types/notes"

interface PageProps {
  params: Promise<{ id: string }>
}

function formatDateTime(value: string | null): string {
  if (!value) return "—"
  return new Date(value).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

function noteHref(patientId: string, note: Note): string {
  return note.session_id
    ? `/dashboard/sessions/${note.session_id}`
    : `/dashboard/patients/${patientId}/notes/${note.id}`
}

function noteStatus(note: Note): { label: string; className: string } {
  if (note.finalized_at) {
    return {
      label: "Finalized",
      className: "bg-secondary-100 text-secondary-700",
    }
  }
  return {
    label: "Draft",
    className: "bg-yellow-100 text-yellow-800",
  }
}

export default function PatientNotesListPage({ params }: PageProps) {
  const { id } = use(params)
  const { data: patient, isLoading: patientLoading } = usePatient(id)
  const { data: notesData, isLoading: notesLoading, error } = usePatientNotes(id)

  if (patientLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-96 w-full" />
      </div>
    )
  }

  if (!patient) {
    return (
      <div className="card text-center py-12">
        <p className="text-red-500">Patient not found.</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <Link
          href={`/dashboard/patients/${patient.id}`}
          className="flex items-center gap-2 text-neutral-600 hover:text-neutral-900 transition-colors"
        >
          <ArrowLeft className="w-5 h-5" />
          <span>Back to {patient.first_name} {patient.last_name}</span>
        </Link>
        <NewNoteButton patientId={patient.id} />
      </div>

      <div>
        <h1 className="text-3xl font-display font-bold text-neutral-900 mb-1">
          Notes
        </h1>
        <p className="text-neutral-600">
          All clinical notes for {patient.first_name} {patient.last_name},
          ordered by most recent.
        </p>
      </div>

      {notesLoading ? (
        <Skeleton className="h-96 w-full" />
      ) : error ? (
        <div className="card text-center py-12">
          <p className="text-red-500">
            {error instanceof Error ? error.message : "Failed to load notes."}
          </p>
        </div>
      ) : !notesData || notesData.total === 0 ? (
        <div className="card text-center py-12">
          <FileText className="w-12 h-12 mx-auto text-neutral-300 mb-3" />
          <p className="text-neutral-600">No notes yet for this patient.</p>
          <p className="text-sm text-neutral-500 mt-1">
            Use the <strong>New note</strong> button to create a standalone
            note, or generate one from a recorded session.
          </p>
        </div>
      ) : (
        <div className="card overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                <th className="px-4 py-3 text-left text-xs font-medium text-neutral-600 uppercase tracking-wider">
                  Type
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-neutral-600 uppercase tracking-wider">
                  Source
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-neutral-600 uppercase tracking-wider">
                  Updated
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-neutral-600 uppercase tracking-wider">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {notesData.data.map((note) => {
                const status = noteStatus(note)
                return (
                  <tr
                    key={note.id}
                    className="border-b border-neutral-100 hover:bg-neutral-50 transition-colors"
                  >
                    <td className="px-4 py-4 text-sm text-neutral-900 capitalize">
                      <Link
                        href={noteHref(patient.id, note)}
                        className="hover:underline"
                      >
                        {note.note_type}
                      </Link>
                    </td>
                    <td className="px-4 py-4 text-sm text-neutral-600">
                      {note.session_id ? "Session" : "Standalone"}
                    </td>
                    <td className="px-4 py-4 text-sm text-neutral-600">
                      {formatDateTime(note.finalized_at ?? note.updated_at)}
                    </td>
                    <td className="px-4 py-4">
                      <span
                        className={`inline-flex px-2 py-1 text-xs font-medium rounded ${status.className}`}
                      >
                        {status.label}
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
