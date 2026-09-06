// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * BillingProfileBanner
 *
 * What claims still need from this page, in one line, above the form. Reads
 * the same gaps a claim review refuses on, so filling the form here is what
 * unblocks the claim that sent the therapist over.
 */

"use client"

import { AlertTriangle, CheckCircle2 } from "lucide-react"
import type { BillingProfileGaps } from "./billingProfileGaps"

interface BillingProfileBannerProps {
  gaps: BillingProfileGaps
  /** The clearinghouse's id for the practice, once registered. */
  registered: boolean
}

export function BillingProfileBanner({ gaps, registered }: BillingProfileBannerProps) {
  const { claims, clearinghouse, advisable } = gaps

  if (claims.length > 0) {
    return (
      <div
        role="status"
        data-testid="billing-profile-banner"
        className="mb-[18px] flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <div>
          <p className="font-semibold">Claims need: {claims.join(", ")}.</p>
          <p className="mt-0.5 text-[12.5px] text-amber-800">
            A claim is refused until these are filled in.
            {clearinghouse.length > 0 &&
              ` Registering with your clearinghouse also needs: ${clearinghouse.join(", ")}.`}
          </p>
        </div>
      </div>
    )
  }

  if (!registered && clearinghouse.length > 0) {
    return (
      <div
        role="status"
        data-testid="billing-profile-banner"
        className="mb-[18px] flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <div>
          <p className="font-semibold">
            Registering with your clearinghouse needs: {clearinghouse.join(", ")}.
          </p>
          <p className="mt-0.5 text-[12.5px] text-amber-800">
            Claims can be built now; enrolling with payers waits on the registration.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div
      role="status"
      data-testid="billing-profile-banner"
      className="mb-[18px] flex items-start gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900"
    >
      <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <div>
        <p className="font-semibold">
          {registered ? "Registered with your clearinghouse." : "Claims have what they need."}
        </p>
        {advisable.length > 0 && (
          <p className="mt-0.5 text-[12.5px] text-emerald-800">
            Some payers also want a {advisable.join(" and ")}.
          </p>
        )}
      </div>
    </div>
  )
}
