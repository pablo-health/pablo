// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { X } from "lucide-react"
import Link from "next/link"
import { useState } from "react"
import { useChecklist } from "@/hooks/useCredentialingChecklist"
import { usePreferences } from "@/hooks/usePreferences"

/**
 * The way into billing setup, for a practice that has not been through it.
 *
 * It points at the wizard rather than asking anything itself. It used to put
 * the question inline — two of the four answers, as links — which meant two
 * surfaces asking the same thing in different words, and the wizard's own
 * first screen asks it better and records what she says.
 *
 * Shown ONLY when the record is silent about her panels. If a payer
 * participation is already on file we know how she is paid, and prompting
 * anyway would break the promise the whole intake rests on: never ask for what
 * we can already read. That is also why no dismissal is stored — going through
 * setup is what makes this go away, and the close button is a courtesy for the
 * current visit rather than a decision worth keeping.
 *
 * TWO ways the question can be settled, and for a while this only knew one.
 * A payer row answers it, but "nobody, I'm paid in cash" is an answer with no
 * row to write it on — so a cash practice finished the wizard, came back to
 * Billing, and found the card inviting it to do the thing it had just done.
 * The wizard records that it ran; read that too.
 *
 * A card above the tabs rather than a gate in front of them. She clicked
 * Billing with work in mind; intercepting her would be a speed bump on the way
 * to it.
 */
export function CredentialingPrompt() {
  const [dismissed, setDismissed] = useState(false)
  const { data: checklist } = useChecklist()
  const { data: preferences } = usePreferences()
  const setupComplete = preferences?.billing_setup_complete ?? false

  const panels = checklist?.fields.find((f) => f.key === "payer_participation")
  // Absent means the question does not apply to her at all; answered means we
  // already know. Either way there is nothing to ask.
  if (dismissed || !panels || panels.answered) return null
  // And she may have been through setup and answered it as "nobody" — which a
  // payer row cannot record, because there is no row to write. A practice paid
  // in cash never adds a payer, so gating on the payer record alone left this
  // card on the Billing page permanently, no matter how many times she
  // finished the wizard it points at.
  if (setupComplete) return null

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
            Finish setting up how you get paid
          </p>
          {/* One sentence, and no pronoun for the client. The version this
              replaces spent its second half saying we hold nothing yet, which
              is the card's own reason for being on screen rather than
              anything the reader needs told. */}
          <p className="mt-1 text-sm text-neutral-600">
            A few questions about your practice, so Pablo knows whether a
            session bills as a claim or a superbill.
          </p>
          <div className="mt-3 text-sm">
            <Link
              href="/dashboard/billing/setup"
              className="font-medium text-neutral-900 underline underline-offset-4 hover:text-neutral-700"
            >
              Set up billing
            </Link>
          </div>
        </div>
      </div>
    </div>
  )
}
