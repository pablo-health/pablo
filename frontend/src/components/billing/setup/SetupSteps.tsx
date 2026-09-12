// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { AppointmentTypesCard } from "@/components/settings/AppointmentTypesCard"
import { BillingContactCard } from "@/components/settings/BillingContactCard"
import { PracticeIdentityCard } from "@/components/settings/PracticeIdentityCard"
import { SetupStepHead } from "@/components/setup"
import { Skeleton } from "@/components/ui/skeleton"
import { useBillingProfile } from "@/hooks/useBillingProfile"

/**
 * The step bodies that are shared by every route.
 *
 * Each one mounts the component Settings already uses rather than a second
 * form against the same fields. Two editors for one record is how the two
 * drift apart — different validation, different labels, a fix applied to one
 * of them — so the wizard borrows the surface instead of copying it.
 */

export function PracticeIdentityStep() {
  const { data: profile } = useBillingProfile()

  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="Practice identity"
        title="How insurers identify your practice"
        lede="The legal and tax details that appear on a claim. You'll only need to enter these once."
      />
      {profile ? <PracticeIdentityCard profile={profile} /> : <Skeleton className="h-64 w-full" />}
    </div>
  )
}

export function BillingContactStep() {
  const { data: profile } = useBillingProfile()

  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="Billing contact"
        title="Where should insurers reach you?"
        lede="Where enrollment questions and payment notices go."
      />
      {profile ? <BillingContactCard profile={profile} /> : <Skeleton className="h-64 w-full" />}
    </div>
  )
}

export function RatesStep() {
  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="Step 3"
        title="What you charge"
        lede="Your session types and their fees. These set what a client owes and what appears on a superbill."
      />
      <AppointmentTypesCard />
    </div>
  )
}

/**
 * Where a private-pay practice finishes.
 *
 * A finish, not a landing. No nag toward insurance, no progress left sitting
 * at two thirds — she has given us everything we need to bill her clients, and
 * taking insurance is an offer she is free to decline forever.
 *
 * The one line about insurance is deliberately quiet and last. The design's
 * re-engagement triggers do the real work later, when four of her clients turn
 * up with the same payer and the argument makes itself.
 */
export function PrivatePayDoneStep() {
  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="All set"
        title="You're set up to get paid"
        lede="Nothing else is owed. Here's how the money moves from here."
      />

      <ul className="space-y-3 text-sm text-neutral-700">
        <li>
          Finalise a session and it lands in{" "}
          <Link
            href="/dashboard/billing"
            className="font-medium underline underline-offset-4"
          >
            Unbilled
          </Link>
          . Charge it there.
        </li>
        <li>
          A client claiming it back from her own insurer gets a superbill — your
          practice details and rates are what fill it in.
        </li>
        <li>
          A charge that fails shows up in Unbilled too, so nothing quietly goes
          unpaid.
        </li>
      </ul>

      <p className="border-t border-border pt-4 text-sm text-muted-foreground">
        If you ever want to bill insurance directly, setup picks up from here —
        most of what a payer asks for is already answered.
      </p>
    </div>
  )
}
