// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { X } from "lucide-react"
import Link from "next/link"
import { useState } from "react"
import { INSURANCE_PAYERS_SETTINGS_PATH } from "@/components/settings/paths"
import { useIntake } from "@/hooks/useCredentialingIntake"

/**
 * The one question that decides how every session on this page gets billed.
 *
 * Whether a clinician is on a payer's panel decides whether a session becomes
 * an insurance claim or a superbill her client files herself. Billing cannot
 * be right without it, and the two answers lead somewhere different: one to
 * recording her contracts, the other to getting her some.
 *
 * It asks how she gets PAID rather than whether she is credentialed, and that
 * is not a wording preference. "Are you credentialed?" is mis-answerable by
 * almost everyone: a clinician with an NPI and a CAQH profile reads that as
 * yes and is contracted with nobody. This codebase already knows that failure
 * — ``payer_participations`` separates ``credentialed`` from ``contracted``
 * precisely because a practice can sit in the first for years believing it is
 * paneled. A wrong yes here routes her into claims that deny. How her clients
 * pay her is something she cannot be wrong about, and the answer is itself the
 * Tier-1 fact we were trying to learn.
 *
 * Asked ONLY when the record is silent. If she already has a payer
 * participation on file we know the answer, and asking anyway would break the
 * promise the whole intake rests on — never ask for what we can already read.
 * That is also why there is no dismissed-flag to store: answering it is what
 * makes it go away, and the close button is a courtesy for this visit rather
 * than a decision worth persisting.
 *
 * A card above the tabs rather than a gate in front of them. She clicked
 * Billing with intent; intercepting it would be a speed bump on the way to
 * work she came to do.
 */
export function CredentialingPrompt() {
  const [dismissed, setDismissed] = useState(false)
  const { data: intake } = useIntake()

  const panels = intake?.fields.find((f) => f.key === "payer_participation")
  // Absent means the question does not apply to her at all; answered means we
  // already know. Either way there is nothing to ask.
  if (dismissed || !panels || panels.answered) return null

  return (
    <div className="relative rounded-xl border border-neutral-200 bg-white p-4">
      <button
        type="button"
        onClick={() => setDismissed(true)}
        aria-label="Dismiss"
        className="absolute right-3 top-3 rounded p-1 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-600"
      >
        <X className="h-4 w-4" aria-hidden />
      </button>

      <div className="pr-8">
        <div className="min-w-0">
          <p className="text-sm font-medium text-neutral-900">
            How do your clients pay you today?
          </p>
          <p className="mt-1 text-sm text-neutral-600">
            It decides how these sessions get billed — as a claim we file, or as
            a superbill your client files herself. We have nothing on file yet
            either way.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
            <Link
              href={INSURANCE_PAYERS_SETTINGS_PATH}
              className="font-medium text-neutral-900 underline underline-offset-4 hover:text-neutral-700"
            >
              Insurance pays me — tell us which
            </Link>
            <Link
              href="/dashboard/credentialing"
              className="font-medium text-neutral-900 underline underline-offset-4 hover:text-neutral-700"
            >
              Clients pay me directly — help me take insurance
            </Link>
          </div>
        </div>
      </div>
    </div>
  )
}
