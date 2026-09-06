// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { SettingsCard } from "../ui"

/**
 * Practice > Patient portal.
 *
 * Gated behind `patient_portal`, so this only renders where a deployment has
 * turned the portal on. The controls arrive with the portal itself; this is the
 * page they will land on.
 */
export function PatientPortalPage() {
  return (
    <SettingsCard title="Patient portal">
      <p className="text-sm text-muted-foreground">
        Intake forms, self-report measures and patient sign-in will be configured here.
      </p>
    </SettingsCard>
  )
}
