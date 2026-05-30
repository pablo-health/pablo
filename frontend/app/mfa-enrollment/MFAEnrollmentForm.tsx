// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Generic MFA-enrollment surface: renders whichever form the active auth
 * provider supplies. Firebase renders its TOTP enrollment flow; OIDC hosts
 * MFA on Keycloak's own pages and renders nothing here.
 *
 * The export name and `returnTo` prop are preserved because the SaaS
 * overlay imports this module directly (`@/app/mfa-enrollment/
 * MFAEnrollmentForm`) for its onboarding wizard.
 */

import { getAuthSurfaces } from "@/lib/auth/surfaces"
import type { MfaEnrollmentFormProps } from "@/lib/auth/types"

export type MFAEnrollmentFormProps = MfaEnrollmentFormProps

export function MFAEnrollmentForm(props: MFAEnrollmentFormProps = {}) {
  const { MfaEnrollmentForm } = getAuthSurfaces()
  return <MfaEnrollmentForm {...props} />
}
