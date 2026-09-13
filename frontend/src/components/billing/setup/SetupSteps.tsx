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

/**
 * Where a clinician who is not on panels yet finishes.
 *
 * Deliberately does NOT say "you're set up to bill", which is what the
 * already-paneled ending says and would be a lie here: no panel has accepted
 * her yet. Saying otherwise would send her looking for claims that cannot
 * exist.
 *
 * Pablo does the credentialing work from here, so this reads as a handover
 * rather than a filing cabinet: what we have, what we still need, and that
 * she will be told where each application stands. The one thing it must not
 * do is imply she is waiting on us before she can practise — she is not, and
 * a superbill pays her out-of-network clients today.
 */
export function WantsPanelsDoneStep() {
  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="Handed over"
        title="Pablo takes it from here"
        lede="We'll put your applications in and chase them. Panels take months to answer, and none of that has to hold up seeing clients."
      />

      <ul className="space-y-3 text-sm text-neutral-700">
        <li>
          You can see clients and be paid today. Finalise a session and Pablo
          produces a superbill your client submits for out-of-network
          reimbursement &mdash; no insurer contract needed.
        </li>
        <li>
          If an application needs something we do not have yet &mdash; a
          document, a signature, a date &mdash; it appears in{" "}
          <Link
            href="/dashboard/settings/credentialing"
            className="font-medium underline underline-offset-4"
          >
            Credentialing
          </Link>
          . You will not have to work out what is missing.
        </li>
        <li>
          Each insurer&rsquo;s progress shows up in the same place, so &ldquo;where
          is my Aetna application&rdquo; is a screen rather than an email.
        </li>
      </ul>

      <p className="border-t border-border pt-4 text-sm text-muted-foreground">
        Nothing here has to be finished before you see clients. We will come to
        you when we need something.
      </p>
    </div>
  )
}

/**
 * Where a clinician moving off a platform finishes.
 *
 * The same facts as WantsPanelsDoneStep, with the one difference that defines
 * this route: she is already seeing clients through somebody else's contracts,
 * so her question is not "how do I start" but "what carries over". Her NPI and
 * her credentialing record do; the platform's contracts do not.
 */
export function PlatformToOwnDoneStep() {
  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="On file"
        title="Your record is yours, whatever the platform holds"
        lede="Contracts in your own name take months to get. Nothing about seeing clients waits on them."
      />

      <ul className="space-y-3 text-sm text-neutral-700">
        <li>
          Your NPI and the answers you just gave belong to you, not to a
          platform. They are what every application in your own name will be
          filled in from.
        </li>
        <li>
          Clients you see outside the platform can be billed today by
          superbill, so your own practice can start before a single contract
          is signed.
        </li>
        <li>
          Pablo puts the applications in and chases them. As each insurer
          contracts with you directly, its progress &mdash; and anything it
          still needs from you &mdash; shows up in{" "}
          <Link
            href="/dashboard/settings/credentialing"
            className="font-medium underline underline-offset-4"
          >
            Credentialing
          </Link>
          .
        </li>
      </ul>

      <p className="border-t border-border pt-4 text-sm text-muted-foreground">
        Keep billing through the platform as long as you need to. We will come
        to you when an application needs something, and nothing here asks you
        to switch before you are ready.
      </p>
    </div>
  )
}

/**
 * Where a practice that is already paneled finishes.
 *
 * She was billing before she met us, so this says what changes rather than
 * congratulating her on arriving. Enrollment is the one thing still moving:
 * it is filed, the payers answer in their own time, and nothing she does
 * makes that faster — so the honest ending says where to watch rather than
 * implying she has something left to do.
 */
export function AlreadyPaneledDoneStep() {
  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="All set"
        title="You're set up to bill"
        lede="Your practice details are on file and your payers are in. Here's what happens next."
      />

      <ul className="space-y-3 text-sm text-neutral-700">
        <li>
          Finalise a session and it lands in{" "}
          <Link href="/dashboard/billing" className="font-medium underline underline-offset-4">
            Unbilled
          </Link>
          . File the claim from there.
        </li>
        <li>
          Enrollment requests sit with each payer until they answer. You do not
          need to chase them — if one wants something from you, it shows up on
          the payer in{" "}
          <Link
            href="/dashboard/settings/insurance"
            className="font-medium underline underline-offset-4"
          >
            Insurance payers
          </Link>
          .
        </li>
        <li>
          A payer you are not enrolled with yet can still be billed by
          superbill, so a client is never stuck waiting on paperwork between us
          and her insurer.
        </li>
      </ul>

      <p className="border-t border-border pt-4 text-sm text-muted-foreground">
        Adding a payer later is the same screen. Nothing here has to be complete
        before you see clients.
      </p>
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
