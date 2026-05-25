// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { Users, Calendar, Phone, Mail } from "lucide-react"
import type { PatientResponse } from "@/types/patients"

interface PatientSummaryProps {
  patient: PatientResponse
}

const STATUS_BADGE_STYLES: Record<string, string> = {
  active: "bg-secondary-100 text-secondary-700",
  inactive: "bg-neutral-100 text-neutral-700",
  on_hold: "bg-yellow-100 text-yellow-700",
}

function formatDate(dateString: string | null): string {
  if (!dateString) return "N/A"
  try {
    return new Date(dateString).toLocaleDateString()
  } catch {
    return "N/A"
  }
}

function StatusBadge({ status }: { status: string }) {
  const style = STATUS_BADGE_STYLES[status] ?? STATUS_BADGE_STYLES.inactive
  return (
    <span className={`inline-flex px-3 py-1 text-sm font-medium rounded-full ${style}`}>
      {status.replace("_", " ")}
    </span>
  )
}

export function PatientSummary({ patient }: PatientSummaryProps) {
  return (
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
            <StatusBadge status={patient.status} />
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
  )
}
