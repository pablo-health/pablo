// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { SetupStepHead } from "@/components/setup"
import type { CurrentStateId } from "./routes"

/**
 * Where setup finishes, assembled from what she told us rather than chosen from
 * a list of endings.
 *
 * There used to be one canned ending per route, which worked only while a
 * therapist could be exactly one thing. She can be several, so the ending is
 * composed the same way the flow is: a headline for her situation, then a line
 * per fact that is true of her.
 *
 * Three things this screen must never do, each of which it did in some form
 * before:
 *
 * - **Promise submission we are not authorised to make.** "We'll put your
 *   applications in" is only true once she has signed the authorisation, so
 *   the credentialing line says what Pablo prepares and tracks.
 * - **Imply she can bill insurance today** because setup finished. Setup being
 *   complete and being able to file a claim are different states, and §4 of the
 *   platform requirement exists because conflating them is how someone starts
 *   seeing clients expecting to be paid.
 * - **Suggest her platform arrangement has changed.** It has not, and the
 *   commonest fear about pointing a second system at your billing is exactly
 *   that it will quietly start moving money.
 */
export function DoneStep({
  selected,
  wantsCredentialing,
}: {
  selected: readonly CurrentStateId[]
  wantsCredentialing: boolean
}) {
  const onPlatform = selected.includes("platform")
  const billsInsurance = selected.includes("own_insurance")
  const takesSelfPay = selected.includes("self_pay")
  const seeingNobodyYet = selected.length === 0

  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="On file"
        title={title({ seeingNobodyYet, onPlatform, wantsCredentialing, billsInsurance })}
        lede={lede({ seeingNobodyYet, onPlatform, wantsCredentialing })}
      />

      <ul className="space-y-3 text-sm text-neutral-700">
        {takesSelfPay && (
          <li>
            You can take payments today. Finalise a session and it appears in{" "}
            <Link
              href="/dashboard/billing"
              className="font-medium underline underline-offset-4"
            >
              Billing
            </Link>{" "}
            to charge, and a client claiming it back from her own insurer gets a superbill your
            practice details fill in.
          </li>
        )}

        {billsInsurance && (
          <>
            <li>
              Finalise a session and it lands in{" "}
              <Link
                href="/dashboard/billing"
                className="font-medium underline underline-offset-4"
              >
                Unbilled
              </Link>
              . File the claim from there.
            </li>
            <li>
              Enrollment requests sit with each payer until they answer. You do not need to chase
              them &mdash; if one wants something from you, it shows up on the payer in{" "}
              <Link
                href="/dashboard/settings/insurance"
                className="font-medium underline underline-offset-4"
              >
                Insurance payers
              </Link>
              .
            </li>
            <li>
              A payer you are not enrolled with yet can still be billed by superbill, so a client
              is never stuck waiting on paperwork between us and her insurer.
            </li>
          </>
        )}

        {wantsCredentialing && (
          <>
            <li>
              Your NPI and the answers you just gave stay on your record, and are what every
              application in your own name is filled in from.
            </li>
            <li>
              Pablo prepares and tracks the applications. Where one needs something only you can
              give &mdash; a signature, a document, a date &mdash; it appears in{" "}
              <Link
                href="/dashboard/settings/credentialing"
                className="font-medium underline underline-offset-4"
              >
                Credentialing
              </Link>
              , so you never have to work out what is missing.
            </li>
            <li>
              Billing insurance through Pablo starts only when you choose it and the payer setup is
              ready. Getting contracted and being able to file a claim are different things, and we
              will not blur them.
            </li>
          </>
        )}

        {seeingNobodyYet && (
          <li>
            When you start seeing clients, add how they pay you from{" "}
            <Link
              href="/dashboard/billing"
              className="font-medium underline underline-offset-4"
            >
              Billing
            </Link>
            . Nothing you have entered has to be entered again.
          </li>
        )}
      </ul>

      {/* Contextual, quiet, and it does not read her contract for her. Pablo
          cannot know what her agreement says, so it points her at it rather
          than interpreting it — and it sits below the fold of the good news
          rather than dominating the screen. */}
      {onPlatform && takesSelfPay && (
        <p className="text-[12.5px] text-muted-foreground" data-testid="platform-agreement-note">
          Before seeing clients outside the service that pays you, it&rsquo;s worth checking your
          agreement with them for anything that applies to your own practice.
        </p>
      )}

      <p className="border-t border-border pt-4 text-sm text-muted-foreground">
        {closing({ onPlatform, wantsCredentialing })}
      </p>
    </div>
  )
}

function title({
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
  if (seeingNobodyYet) return "Ready for your first client"
  // Deliberately not "your record is yours, whatever the platform holds" — a
  // strong line that implies we hold, retrieved, or can separate something the
  // platform has. We cannot, and saying so would be a promise about somebody
  // else's system.
  if (onPlatform && wantsCredentialing) {
    return "Your own practice can take shape while the platform keeps working"
  }
  if (onPlatform) return "Set up alongside the service that pays you"
  if (wantsCredentialing) return "Pablo takes the applications from here"
  if (billsInsurance) return "You're set up to bill"
  return "You're set up to get paid"
}

function lede({
  seeingNobodyYet,
  onPlatform,
  wantsCredentialing,
}: {
  seeingNobodyYet: boolean
  onPlatform: boolean
  wantsCredentialing: boolean
}): string {
  if (seeingNobodyYet) {
    return "Everything Pablo needs is on file. Add how clients pay you when you have your first."
  }
  if (wantsCredentialing) {
    return onPlatform
      ? "Contracts in your own name take months. You can build them without changing how your current clients are billed."
      : "Contracts take months to come through, and none of it has to hold up seeing clients."
  }
  return "That's everything Pablo needs to get you paid for the work you do."
}

function closing({
  onPlatform,
  wantsCredentialing,
}: {
  onPlatform: boolean
  wantsCredentialing: boolean
}): string {
  if (onPlatform && wantsCredentialing) {
    return "Nothing here changes how you're billed today. When you're ready to bill independently, Pablo will walk you through the claims, payment and remittance setup before anything moves."
  }
  if (onPlatform) {
    return "Keep using the service for the clients it handles. Pablo supports the work you do outside it without changing that arrangement."
  }
  if (wantsCredentialing) {
    return "Nothing here has to be finished before you see clients. We'll come to you when we need something."
  }
  return "If you ever want to bill insurance directly, setup picks up from here — most of what a payer asks for is already answered."
}
