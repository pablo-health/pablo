// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { AppointmentStatus } from "@/types/scheduling"

const STATUS_CONFIG: { status: AppointmentStatus; label: string; color: string }[] = [
  { status: "confirmed", label: "Confirmed", color: "bg-accent-400" },
  { status: "completed", label: "Completed", color: "bg-secondary-400" },
  { status: "cancelled", label: "Cancelled", color: "bg-neutral-400" },
  { status: "no_show", label: "No Show", color: "bg-red-400" },
]

export function StatusLegend() {
  return (
    <div role="list" aria-label="Appointment status legend" className="flex flex-wrap gap-4">
      {STATUS_CONFIG.map(({ status, label, color }) => (
        <div key={status} role="listitem" className="flex items-center gap-2 text-sm text-neutral-600">
          <span className={`inline-block h-3 w-3 rounded-full ${color}`} aria-hidden="true" />
          {label}
        </div>
      ))}
    </div>
  )
}
