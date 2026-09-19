// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { SetupStepHead } from "@/components/setup"
import { billingProfileGaps } from "@/components/settings/billingProfileGaps"
import { useSettingsUserStatus } from "@/components/settings/useSettingsPreferences"
import { useBillingProfile } from "@/hooks/useBillingProfile"
import type { CurrentStateId } from "./routes"
import { usePaymentsConnected } from "./setupSlots.extensions"

/**
 * Where setup finishes, assembled from what was chosen AND from what is
 * actually ready.
 *
 * Two separate jobs, and the second is the one that used to be missing.
 *
 * **Assembled, not chosen.** There was once a canned ending per route, which
 * worked only while a therapist could be exactly one thing. Several can be
 * true at once, so the ending is composed the way the flow is.
 *
 * **Honest about readiness.** The wizard lets every step be skipped — that is
 * deliberate, and it means REACHING this screen proves nothing about whether
 * the practice can be paid. So the completion wording asks
 * ``billingProfileGaps``, the same helper the settings banner uses and the
 * same fields a claim is refused without. "You're set up to bill" over a
 * profile missing a tax id is the worst sentence on this screen: it sends
 * someone off to see clients believing a thing that is not true.
 *
 * Three claims this screen may never make, each of which some earlier version
 * made:
 *
 * - **Submission we are not authorised to make.** Pablo cannot sign anyone's
 *   name to a payer's form until they have authorised it, so the credentialing
 *   lines say prepare and track, and say the authorisation gate out loud.
 * - **That finishing setup means insurance can be billed.** Being contracted
 *   and being able to file a claim are different states.
 * - **That a platform arrangement has changed.** It has not.
 * - **That a client can pay by card.** Taking a card needs a processor, and
 *   ticking "card, cash, bank transfer" on the first screen says how the
 *   practice is paid today, not that one is connected.
 */
export function DoneStep({
  selected,
  wantsCredentialing,
}: {
  selected: readonly CurrentStateId[]
  wantsCredentialing: boolean
}) {
  const { data: profile } = useBillingProfile()
  const { data: user } = useSettingsUserStatus()
  // `null` on a deployment with no processor concept, and while the read is in
  // flight. Only an explicit `false` is grounds for saying something is left.
  const paymentsConnected = usePaymentsConnected()

  const onPlatform = selected.includes("platform")
  const billsInsurance = selected.includes("own_insurance")
  const takesDirectPay = selected.includes("self_pay")
  const seeingNobodyYet = selected.length === 0

  const gaps = profile
    ? billingProfileGaps(profile, {
        npi_number: user?.npi_number ?? null,
        taxonomy_code: user?.taxonomy_code ?? null,
      })
    : null
  // Unknown counts as not-ready. Both reads are in flight for a moment, and
  // claiming readiness we have not checked is the failure this whole section
  // exists to avoid.
  const profileReady = gaps !== null && gaps.claims.length === 0
  const billingReady = profileReady && gaps !== null && gaps.clearinghouse.length === 0
  // A superbill carries the rendering provider's NPI and cannot be produced
  // without one — ``_RENDERING_PROVIDER_REQUIRED`` in superbill.py. Promising
  // one to somebody who has not given us an NPI is discovered by a client
  // asking for reimbursement paperwork.
  const superbillReady = Boolean(user?.npi_number)

  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="On file"
        title={title({
          seeingNobodyYet,
          onPlatform,
          wantsCredentialing,
          billsInsurance,
          billingReady,
          profileReady,
        })}
        lede={lede({ seeingNobodyYet, onPlatform, wantsCredentialing, billsInsurance })}
      />

      <ul className="space-y-3 text-sm text-neutral-700">
        {takesDirectPay && (
          <li>
            Finalize a session and it appears in{" "}
            <Link href="/dashboard/billing" className="font-medium underline underline-offset-4">
              Billing
            </Link>
            {paymentsConnected === false ? (
              <>. Clients can pay by card once you connect a payment processor.</>
            ) : (
              <>, ready to charge.</>
            )}
          </li>
        )}

        {takesDirectPay && superbillReady && (
          <li>When a client needs a superbill, Pablo prepares it from your practice details.</li>
        )}

        {billsInsurance && (
          <>
            {/* Two sentences became one. "Enrollment requests sit with each
                payer until they answer" describes our side of a queue nobody
                asked about, and "you do not need to chase them" is
                reassurance against a worry the sentence itself introduced. */}
            <li>
              If a payer needs something from you, it shows up on that payer in{" "}
              <Link
                href="/dashboard/settings/insurance"
                className="font-medium underline underline-offset-4"
              >
                Insurance payers
              </Link>
              .
            </li>
            <li>While an application is pending, you can still bill a client by superbill.</li>
          </>
        )}

        {wantsCredentialing && (
          <>
            <li>Your NPI and credentialing details are reused for every application.</li>
            <li>
              Pablo prepares and tracks applications. If one needs your signature or a document,
              you&rsquo;ll see it in{" "}
              <Link
                href="/dashboard/settings/credentialing"
                className="font-medium underline underline-offset-4"
              >
                Credentialing
              </Link>
              .
            </li>
            <li>Pablo won&rsquo;t submit an application until you authorize it.</li>
            <li>
              Billing a payer through Pablo comes later, once your contract and billing setup are
              ready.
            </li>
          </>
        )}
      </ul>

      {/* Points at the agreement rather than reading it. Pablo cannot know what
          it says, and an app that interprets somebody's contract for them is
          worse than one that reminds them it exists. */}
      {onPlatform && takesDirectPay && (
        <p className="text-[12.5px] text-muted-foreground" data-testid="platform-agreement-note">
          If you start seeing clients outside the service, check its agreement for any restrictions
          first.
        </p>
      )}

      {/* Said only when it is true. The wizard permits skipped fields, so the
          honest ending for an incomplete profile is that the work is saved,
          not that it is done. */}
      {!profileReady && !seeingNobodyYet && (
        <p className="text-[12.5px] text-muted-foreground" data-testid="setup-incomplete">
          A few details are still missing. You can see what&rsquo;s left in{" "}
          <Link
            href="/dashboard/settings/billing-profile"
            className="font-medium underline underline-offset-4"
          >
            Practice identity
          </Link>
          .
        </p>
      )}

      <p className="border-t border-border pt-4 text-sm text-muted-foreground">
        {closing({ seeingNobodyYet, onPlatform, wantsCredentialing })}
      </p>
    </div>
  )
}

function title({
  seeingNobodyYet,
  onPlatform,
  wantsCredentialing,
  billsInsurance,
  billingReady,
  profileReady,
}: {
  seeingNobodyYet: boolean
  onPlatform: boolean
  wantsCredentialing: boolean
  billsInsurance: boolean
  billingReady: boolean
  profileReady: boolean
}): string {
  if (seeingNobodyYet) return "Ready when you are"
  // Both platform headings say the same true thing, and the lede below does
  // the distinguishing. They used to promise an outcome — building your own
  // contracts, being set up for outside work — which is a claim about where
  // this leaves you rather than about what happened.
  if (onPlatform) return "Everything you entered is saved"
  // "is ready" was a readiness claim reaching this screen does not support:
  // the wizard lets every field be skipped.
  if (wantsCredentialing) return "Your credentialing record is saved"
  // Both of these used to be stated unconditionally, which made them a claim
  // about readiness that arriving here does not support.
  if (billsInsurance) return billingReady ? "You're set up to bill" : "Your billing setup is underway"
  return profileReady ? "You're ready to take direct payments" : "Your direct-payment setup is saved"
}

function lede({
  seeingNobodyYet,
  onPlatform,
  wantsCredentialing,
  billsInsurance,
}: {
  seeingNobodyYet: boolean
  onPlatform: boolean
  wantsCredentialing: boolean
  billsInsurance: boolean
}): string {
  if (seeingNobodyYet) {
    return "What you entered is saved. You can finish setting up payments when you start seeing clients."
  }
  // NAMES what "the service" is, on its first mention, because this is the
  // first line the reader meets. "The service" alone could be Pablo, a
  // clearinghouse, or the company that pays them; only the last is meant, and
  // the sentence has to say so without suggesting they leave it.
  if (onPlatform && wantsCredentialing) {
    return "You can get ready to bill on your own while the service that bills for you today keeps doing that."
  }
  if (onPlatform) {
    return "The service that bills for you today keeps doing that. Pablo can handle the work you do outside it."
  }
  if (wantsCredentialing) {
    return "You can prepare applications now and decide when you're ready to send them."
  }
  if (billsInsurance) {
    return "We saved what you entered. Each payer shows what is ready and what still needs attention."
  }
  return "We saved what you entered."
}

function closing({
  seeingNobodyYet,
  onPlatform,
  wantsCredentialing,
}: {
  seeingNobodyYet: boolean
  onPlatform: boolean
  wantsCredentialing: boolean
}): string {
  if (seeingNobodyYet) return "You won't need to start over."
  if (onPlatform) {
    // The first sentence used to be "keep using the service for as long as it
    // works for you" — kindly meant, and the lede has already said it. What is
    // left is the one thing this screen owes them: nothing about where their
    // money goes moves without being shown to them first.
    return "Before Pablo changes where a payer sends claims, payments, or payment reports, you'll see what would change and decide whether to go ahead."
  }
  if (wantsCredentialing) {
    // Was a restatement of the bullet above it. The payoff is the thing worth
    // ending on instead.
    return "Every application asks for the same facts. You've now answered them once."
  }
  return "If you ever want to bill insurance directly, setup picks up from here — most of what a payer asks for is already answered."
}
