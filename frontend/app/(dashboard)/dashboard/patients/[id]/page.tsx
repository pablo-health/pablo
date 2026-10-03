// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { use } from "react"
import { ArrowLeft } from "lucide-react"
import Link from "next/link"
import { EditPatientButton } from "@/components/patients/EditPatientButton"
import { PatientExport } from "@/components/patients/PatientExport"
import { PatientSummary } from "@/components/patients/PatientSummary"
import { PatientChartTabs } from "@/components/patients/PatientChartTabs"
import { PatientChatDialog } from "@/components/patients/PatientChatDialog"
import { PatientChartExtras } from "@/components/patients/PatientChartExtras"
import { NewNoteButton } from "@/components/notes/NewNoteButton"
import { usePatient } from "@/hooks/usePatients"

interface PatientDetailPageProps {
  params: Promise<{
    id: string
  }>
  /** `?tab=intake` opens the chart on that tab — a link from elsewhere in
   * the app can land somebody on the part of the chart it is about. */
  searchParams?: Promise<{ tab?: string }>
}

const NO_SEARCH = Promise.resolve({})

export default function PatientDetailPage({ params, searchParams }: PatientDetailPageProps) {
  const { id } = use(params)
  const { tab } = use<{ tab?: string }>(searchParams ?? NO_SEARCH)
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
            <span>Back to Clients</span>
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
            <span>Back to Clients</span>
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
          <span>Back to Clients</span>
        </Link>
        <div className="flex items-center gap-2">
          <EditPatientButton patient={patient} />
          <PatientChatDialog patientId={patient.id} />
          <NewNoteButton patientId={patient.id} />
          <PatientExport
            patientId={patient.id}
            patientName={`${patient.first_name} ${patient.last_name}`}
          />
        </div>
      </div>

      <PatientSummary patient={patient} />

      <PatientChartTabs patientId={patient.id} initialTab={tab} />

      <PatientChartExtras patientId={patient.id} />
    </div>
  )
}
