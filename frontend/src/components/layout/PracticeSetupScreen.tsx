// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useSyncExternalStore } from "react"
import { PabloSpinner } from "@/components/ui/PabloSpinner"
import {
  isPracticeSetupInProgress,
  isPracticeSetupInProgressOnServer,
  subscribePracticeSetup,
} from "@/lib/api/practiceSetup"

/**
 * Covers the page while a request waits for the signed-in practice to be set
 * up (see `practiceSetup.ts`), so the clinician sees one calm state instead
 * of panels that each spin or fail. Renders nothing otherwise.
 *
 * It leaves when the waiting request gets its answer. The copy only says
 * setup is underway: whether it finished is for that answer to show, not for
 * this screen to announce.
 */
export function PracticeSetupScreen() {
  const settingUp = useSyncExternalStore(
    subscribePracticeSetup,
    isPracticeSetupInProgress,
    isPracticeSetupInProgressOnServer,
  )
  if (!settingUp) return null
  return (
    <div
      data-testid="practice-setup-screen"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-background"
    >
      <PabloSpinner label="Setting up your practice" size={48} />
    </div>
  )
}
