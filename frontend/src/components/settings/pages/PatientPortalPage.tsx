// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { IntakeDocumentsCard } from "../intake/IntakeDocumentsCard"
import { IntakeFormsCard } from "../intake/IntakeFormsCard"
import { SettingsCard } from "../ui"

/**
 * Practice > Patient portal.
 *
 * Gated behind `patient_portal`, so this only renders where a deployment has
 * turned the portal on. Forms and the documents they can ask somebody to
 * sign are here; sign-in and the rest of the portal's controls arrive with
 * the surfaces they belong to.
 *
 * Documents come after forms because that is the order a practice meets
 * them: the form is the thing being built, and a document is something a
 * question on it points at.
 */
export function PatientPortalPage() {
  return (
    <>
      <IntakeFormsCard />
      <IntakeDocumentsCard />
      <SettingsCard title="Patient sign-in">
        <p className="text-sm text-muted-foreground">
          How people get into the portal will be configured here.
        </p>
      </SettingsCard>
    </>
  )
}
