// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Billing setup.
 *
 * Under Billing rather than behind a nav item of its own: getting paid is one
 * job, whether the money comes from a client's card or an insurer's remittance,
 * and splitting it in two would ask a therapist to know which half of the
 * product her question belonged to before she could ask it.
 *
 * A separate route rather than rendered in place of the billing tabs, for now.
 * The wizard does not persist her answer yet, so a practice that had already
 * finished setting up would meet it again on every visit. When the answer is
 * stored, this moves inside the page the way the calendar wizard does.
 */

"use client"

import { GetPaidWizard } from "@/components/billing/setup/GetPaidWizard"

export default function BillingSetupPage() {
  return <GetPaidWizard />
}
