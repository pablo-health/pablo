// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { SupportContactLine } from "@/components/support/SupportContactLine"
import { useSupportEmail } from "@/lib/support"

/** The page footer's contact line; absent, border and all, when no address is configured. */
export function MfaSupportFooter() {
  if (!useSupportEmail()) return null
  return (
    <footer className="mt-8 pt-6 border-t border-neutral-200 text-center">
      <SupportContactLine lead="Questions about MFA?" className="text-sm text-neutral-600" />
    </footer>
  )
}
