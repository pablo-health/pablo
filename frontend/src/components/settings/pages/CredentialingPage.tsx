// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { CredentialingWizard } from "@/components/credentialing/CredentialingWizard"

/**
 * Billing > Credentialing.
 *
 * The same component the billing setup wizard mounts, not a second view of the
 * same checklist. Two surfaces over one record is how the two drift apart —
 * different labels, different validation, a fix applied to one of them — which
 * is the reason `SetupSteps` mounts `PracticeIdentityCard` rather than
 * rebuilding it, and the reason this page is four lines long.
 *
 * A settings item rather than a nav item, and deliberately. Credentialing had
 * its own place in the sidebar for a few hours and it was taken back out: one
 * nav item for getting paid, with everything it needs underneath. This sits
 * beside Practice identity and Billing contact because a therapist thinking
 * "what do the insurers still need from me" is in one place, not three.
 */
export function CredentialingPage() {
  return <CredentialingWizard />
}
