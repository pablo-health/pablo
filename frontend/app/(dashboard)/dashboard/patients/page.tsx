// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { PatientsView } from "@/components/patients/PatientsView"

export default function PatientsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-display font-bold text-neutral-900">
          Patients
        </h1>
        <p className="text-neutral-600 mt-2">
          Manage your patient information
        </p>
      </div>

      <PatientsView />
    </div>
  )
}
