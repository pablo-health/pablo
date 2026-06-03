// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { Activity, FileText, Folder, Stethoscope } from "lucide-react"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Skeleton } from "@/components/ui/skeleton"
import { NewNoteButton } from "@/components/notes/NewNoteButton"
import { PatientDocuments } from "@/components/patients/PatientDocuments"
import { OutcomeMeasuresTab } from "@/components/outcomeMeasures/OutcomeMeasuresTab"
import { DiagnosesTab } from "@/components/diagnoses/DiagnosesTab"
import { usePatientNotes } from "@/hooks/useNotes"
import { usePatientDocuments } from "@/hooks/usePatientDocuments"
import { usePatientOutcomeMeasures } from "@/hooks/useOutcomeMeasures"
import { usePatientDiagnoses } from "@/hooks/useDiagnoses"
import { formatNoteDateTime, noteHref, noteStatus } from "@/lib/noteDisplay"

const PREVIEW_LIMIT = 3

interface PatientChartTabsProps {
  patientId: string
}

function CountBadge({ count }: { count: number }) {
  if (count === 0) return null
  return (
    <span className="ml-1 rounded-full bg-neutral-200 px-1.5 py-0.5 text-xs font-medium text-neutral-700">
      {count}
    </span>
  )
}

function NotesTab({ patientId }: { patientId: string }) {
  const { data, isLoading, error } = usePatientNotes(patientId)

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: PREVIEW_LIMIT }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    )
  }

  if (error) {
    return (
      <p className="text-sm text-red-500">
        {error instanceof Error ? error.message : "Failed to load notes."}
      </p>
    )
  }

  if (!data || data.total === 0) {
    return (
      <div className="flex flex-col items-center gap-3 py-8 text-center">
        <FileText className="h-8 w-8 text-neutral-300" />
        <p className="text-sm text-neutral-600">No notes yet for this patient.</p>
        <NewNoteButton patientId={patientId} />
      </div>
    )
  }

  const recent = data.data.slice(0, PREVIEW_LIMIT)

  return (
    <div className="space-y-3">
      <ul className="space-y-2">
        {recent.map((note) => {
          const status = noteStatus(note)
          return (
            <li key={note.id}>
              <Link
                href={noteHref(patientId, note)}
                className="flex items-center justify-between gap-3 rounded-lg border border-neutral-100 px-3 py-2.5 transition-colors hover:border-primary-200 hover:bg-primary-50/40"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span className="inline-flex items-center rounded bg-neutral-100 px-2 py-0.5 text-xs font-medium capitalize text-neutral-700">
                    {note.note_type}
                  </span>
                  <span className="text-xs text-neutral-500">
                    {note.session_id ? "Session" : "Standalone"}
                  </span>
                </span>
                <span className="flex shrink-0 items-center gap-3">
                  <span className="text-xs text-neutral-500">
                    {formatNoteDateTime(note.finalized_at ?? note.updated_at)}
                  </span>
                  <span
                    className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${status.className}`}
                  >
                    {status.label}
                  </span>
                </span>
              </Link>
            </li>
          )
        })}
      </ul>
      <div className="flex items-center justify-between pt-1">
        <Link
          href={`/dashboard/patients/${patientId}/notes`}
          className="inline-flex items-center gap-1 text-sm text-primary-700 transition-colors hover:text-primary-900"
        >
          <FileText className="h-4 w-4" />
          View all notes
        </Link>
        <NewNoteButton patientId={patientId} />
      </div>
    </div>
  )
}

export function PatientChartTabs({ patientId }: PatientChartTabsProps) {
  const { data: notes } = usePatientNotes(patientId)
  const { data: documents } = usePatientDocuments(patientId)
  const { data: measures } = usePatientOutcomeMeasures(patientId)
  const { data: diagnoses } = usePatientDiagnoses(patientId)

  const noteCount = notes?.total ?? 0
  const documentCount = documents?.total ?? 0
  const measureCount = measures?.total ?? 0
  const diagnosisCount = diagnoses?.total ?? 0

  return (
    <div className="card">
      <h2 className="mb-4 text-xl font-display font-bold text-neutral-900">
        Chart
      </h2>
      <Tabs defaultValue="notes">
        <TabsList>
          <TabsTrigger value="notes">
            <FileText className="h-4 w-4" />
            Notes
            <CountBadge count={noteCount} />
          </TabsTrigger>
          <TabsTrigger value="documents">
            <Folder className="h-4 w-4" />
            Documents
            <CountBadge count={documentCount} />
          </TabsTrigger>
          <TabsTrigger value="measures">
            <Activity className="h-4 w-4" />
            Measures
            <CountBadge count={measureCount} />
          </TabsTrigger>
          <TabsTrigger value="diagnoses">
            <Stethoscope className="h-4 w-4" />
            Diagnoses
            <CountBadge count={diagnosisCount} />
          </TabsTrigger>
        </TabsList>
        <TabsContent value="notes" className="pt-4">
          <NotesTab patientId={patientId} />
        </TabsContent>
        <TabsContent value="documents" className="pt-4">
          <PatientDocuments patientId={patientId} />
        </TabsContent>
        <TabsContent value="measures" className="pt-4">
          <OutcomeMeasuresTab patientId={patientId} />
        </TabsContent>
        <TabsContent value="diagnoses" className="pt-4">
          <DiagnosesTab patientId={patientId} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
