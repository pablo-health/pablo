// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Credentialing
 *
 * Getting onto insurance panels: the one-sitting intake that collects, once,
 * nearly everything any payer will ask for. Its own top-level surface rather
 * than a settings page, because it is a project with state and deadlines
 * rather than something you configure and forget — and because a clinician may
 * buy Pablo for this without ever using it to bill, which makes filing it
 * under billing settings the wrong shape.
 */

"use client"

import { useState } from "react"
import { CredentialingIntro } from "@/components/credentialing/CredentialingIntro"
import { IntakeWizard } from "@/components/credentialing/IntakeWizard"
import { useIntake } from "@/hooks/useCredentialingIntake"

export default function CredentialingPage() {
  const { data: intake, isLoading } = useIntake()
  const [started, setStarted] = useState(false)

  // "First time" is read off the record rather than remembered in a flag: she
  // has answered nothing anywhere, on any surface. Nothing to persist, and a
  // clinician who filled in her licences under compliance last month is not
  // greeted as a newcomer.
  const untouched =
    !isLoading &&
    intake !== undefined &&
    intake.progress.every((tier) => tier.answered === 0)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-display font-semibold text-neutral-900">
          Credentialing
        </h1>
        <p className="mt-1 text-sm text-neutral-600">
          Everything a payer asks for, answered once.
        </p>
      </div>

      {untouched && !started ? (
        <CredentialingIntro onStart={() => setStarted(true)} />
      ) : (
        <IntakeWizard />
      )}
    </div>
  )
}
