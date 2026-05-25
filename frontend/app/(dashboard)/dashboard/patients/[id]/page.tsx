// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { use } from "react"
import { ArrowLeft, FileText } from "lucide-react"
import Link from "next/link"
import { PatientExport } from "@/components/patients/PatientExport"
import { PatientSummary } from "@/components/patients/PatientSummary"
import { PatientChartTabs } from "@/components/patients/PatientChartTabs"
import { PatientChartExtras } from "@/components/patients/PatientChartExtras"
import { PatientDocuments } from "@/components/patients/PatientDocuments"
import { NewNoteButton } from "@/components/notes/NewNoteButton"
import { usePatient } from "@/hooks/usePatients"

interface PatientDetailPageProps {
  params: Promise<{
    id: string
  }>
}

export default function PatientDetailPage({ params }: PatientDetailPageProps) {
  const { id } = use(params)
  const { data: patient, isLoading, error } = usePatient(id)

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Link
            href="/dashboard/patients"
            className="flex items-center gap-2 text-neutral-600 hover:text-neutral-900 transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
            <span>Back to Patients</span>
          </Link>
        </div>
        <div className="card text-center py-12">
          <p className="text-neutral-500">Loading patient details...</p>
        </div>
      </div>
    )
  }

  if (error || !patient) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Link
            href="/dashboard/patients"
            className="flex items-center gap-2 text-neutral-600 hover:text-neutral-900 transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
            <span>Back to Patients</span>
          </Link>
        </div>
        <div className="card text-center py-12">
          <p className="text-red-500">
            {error ? "Failed to load patient details." : "Patient not found"}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header with back button */}
      <div className="flex items-center justify-between">
        <Link
          href="/dashboard/patients"
          className="flex items-center gap-2 text-neutral-600 hover:text-neutral-900 transition-colors"
        >
          <ArrowLeft className="w-5 h-5" />
          <span>Back to Patients</span>
        </Link>
        <div className="flex items-center gap-2">
          <NewNoteButton patientId={patient.id} />
          <PatientExport
            patientId={patient.id}
            patientName={`${patient.first_name} ${patient.last_name}`}
          />
        </div>
      </div>

      <PatientSummary patient={patient} />

      <PatientChartTabs patientId={patient.id} />

      {/* Notes */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-display font-bold text-neutral-900">
            Notes
          </h2>
          <Link
            href={`/dashboard/patients/${patient.id}/notes`}
            className="text-sm text-primary-700 hover:text-primary-900 inline-flex items-center gap-1"
          >
            <FileText className="w-4 h-4" />
            View all notes
          </Link>
        </div>
        <p className="text-neutral-500 text-sm">
          Click <strong>New note</strong> above to start a standalone note for
          this patient, or open the notes list to review prior session notes.
        </p>
      </div>

      <PatientDocuments patientId={patient.id} />

      <PatientChartExtras patientId={patient.id} />
    </div>
  )
}
