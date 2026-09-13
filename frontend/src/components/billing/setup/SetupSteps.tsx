// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { AppointmentTypesCard } from "@/components/settings/AppointmentTypesCard"
import { CredentialingWizard } from "@/components/credentialing/CredentialingWizard"
import { PayersCard } from "@/components/settings/PayersCard"
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
 * Who she can bill, and what the clearinghouse still needs from her.
 *
 * The same card Settings mounts, because it already is the enrollment surface:
 * per payer it shows the requests filed through the clearinghouse, what the
 * payer is waiting on, and the form for answering it. Rebuilding any of that
 * here would be a second way to answer one payer.
 *
 * Enrollment is why this step exists at all, and why it sits where it does.
 * It runs in two layers: the practice registers once with the clearinghouse
 * (``ensure_provider_record``, which needs a complete billing profile — hence
 * the practice steps before this one), and then one request per payer, and
 * sometimes several per payer, because a payer can enrol claims, remittance
 * and eligibility separately. None of it can be filed until she says who she
 * bills, which is what this screen asks.
 */
export function PayersStep() {
  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="Payers"
        title="Who can you bill today?"
        lede="Add the insurers you're contracted with. Pablo files the electronic enrollment each one needs, and tells you when a payer wants something back."
      />
      <PayersCard />
    </div>
  )
}

/**
 * The credentialing checklist, inside the wizard.
 *
 * Mounts `CredentialingWizard` rather than rebuilding it, for the reason
 * PayersStep mounts PayersCard: two editors for one record is how the two
 * drift, and this record is the one a payer application is filled in from.
 * Settings > Credentialing is the same screen, reachable afterwards.
 *
 * The lede says "reuse" because that is the actual payoff. Every payer
 * application asks for the same twenty facts in a different order; answering
 * them once is the thing Pablo is for on this route.
 */
export function CredentialingRecordStep() {
  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="Your record"
        title="The facts every payer will ask you for"
        lede="Answer these once and Pablo keeps them. Every application wants the same things in a different order, so this is the last time you type them."
      />
      <CredentialingWizard />
    </div>
  )
}
