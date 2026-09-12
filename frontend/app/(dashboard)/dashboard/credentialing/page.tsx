// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Getting paid — setup.
 *
 * Temporarily mounted on the credentialing route while the unified wizard is
 * reviewed. Its home is the Billing surface; this route exists so the flow can
 * be walked without disturbing the billing page's own specs, and goes away
 * when the wizard moves.
 */

"use client"

import { GetPaidWizard } from "@/components/billing/setup/GetPaidWizard"

export default function CredentialingPage() {
  return <GetPaidWizard />
}
