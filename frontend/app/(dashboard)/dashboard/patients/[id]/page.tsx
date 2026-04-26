// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { use } from "react"
import { Users, Calendar, Phone, Mail, ArrowLeft, FileText } from "lucide-react"
import Link from "next/link"
import { PatientExport } from "@/components/patients/PatientExport"
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

  const formatDate = (dateString: string | null) => {
    if (!dateString) return "N/A"
    try {
      return new Date(dateString).toLocaleDateString()
    } catch {
      return "N/A"
    }
  }

  const getStatusBadge = (status: string) => {
    const styles = {
      active: "bg-secondary-100 text-secondary-700",
      inactive: "bg-neutral-100 text-neutral-700",
      on_hold: "bg-yellow-100 text-yellow-700",
    }
    const style = styles[status as keyof typeof styles] || styles.inactive

    return (
      <span className={`inline-flex px-3 py-1 text-sm font-medium rounded-full ${style}`}>
        {status.replace("_", " ")}
      </span>
    )
  }

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

      {/* Patient Info Card */}
      <div className="card">
        <div className="flex items-start gap-6">
          <div className="w-20 h-20 rounded-full bg-primary-100 flex items-center justify-center flex-shrink-0">
            <Users className="w-10 h-10 text-primary-600" />
          </div>
          <div className="flex-1">
            <div className="flex items-center gap-4 mb-4">
              <h1 className="text-3xl font-display font-bold text-neutral-900">
                {patient.first_name} {patient.last_name}
              </h1>
              {getStatusBadge(patient.status)}
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-neutral-600">
              <div className="flex items-center gap-2">
                <Mail className="w-4 h-4" />
                <span>{patient.email || "No email provided"}</span>
              </div>
              <div className="flex items-center gap-2">
                <Phone className="w-4 h-4" />
                <span>{patient.phone || "No phone provided"}</span>
              </div>
              <div className="flex items-center gap-2">
                <Calendar className="w-4 h-4" />
                <span>DOB: {formatDate(patient.date_of_birth)}</span>
              </div>
              <div className="flex items-center gap-2">
                <Calendar className="w-4 h-4" />
                <span>Total Sessions: {patient.session_count}</span>
              </div>
              {patient.diagnosis && (
                <div className="flex items-center gap-2 col-span-2">
                  <span className="font-semibold">Diagnosis:</span>
                  <span>{patient.diagnosis}</span>
                </div>
              )}
              {patient.last_session_date && (
                <div className="flex items-center gap-2">
                  <Calendar className="w-4 h-4" />
                  <span>Last Session: {formatDate(patient.last_session_date)}</span>
                </div>
              )}
              {patient.next_session_date && (
                <div className="flex items-center gap-2">
                  <Calendar className="w-4 h-4" />
                  <span>Next Session: {formatDate(patient.next_session_date)}</span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

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
    </div>
  )
}
