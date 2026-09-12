// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { X } from "lucide-react"
import Link from "next/link"
import { useState } from "react"
import { useChecklist } from "@/hooks/useCredentialingChecklist"

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
 * A card above the tabs rather than a gate in front of them. She clicked
 * Billing with work in mind; intercepting her would be a speed bump on the way
 * to it.
 */
export function CredentialingPrompt() {
  const [dismissed, setDismissed] = useState(false)
  const { data: checklist } = useChecklist()

  const panels = checklist?.fields.find((f) => f.key === "payer_participation")
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
            Finish setting up how you get paid
          </p>
          <p className="mt-1 text-sm text-neutral-600">
            A few questions about your practice. They decide whether a session
            bills as a claim we file or a superbill your client files herself,
            and we have nothing on file yet either way.
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
