// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Image from "next/image"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { INSURANCE_PAYERS_SETTINGS_PATH } from "@/components/settings/paths"

interface CredentialingIntroProps {
  onStart: () => void
}

/**
 * What credentialing is, for someone who has never done it.
 *
 * Shown only when the record is empty, because a clinician who has already
 * started does not need the term explained again. The alternative — picking a
 * friendlier word for the nav item and hoping she infers the rest — trades one
 * confusion for another: "credentialing" is the word every payer, CAQH and
 * state board uses, so she will meet it within a day of starting. Better to
 * teach it once, here, than to have her learn it from a rejection letter.
 *
 * The other door matters as much. A therapist who is already on panels does
 * not need any of this, and the worst outcome is her working through an
 * explainer for a process she finished years ago.
 */
export function CredentialingIntro({ onStart }: CredentialingIntroProps) {
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div className="flex items-start gap-4">
        <Image
          src="/pablo-tie.webp"
          alt=""
          width={56}
          height={56}
          className="shrink-0"
        />
        <div>
          <h2 className="text-xl font-display font-semibold text-neutral-900">
            Getting on insurance panels
          </h2>
          <p className="mt-1 text-sm text-neutral-600">
            Also called <span className="font-medium">credentialing</span> — the
            word payers, CAQH and your state board all use for it.
          </p>
        </div>
      </div>

      <div className="space-y-4 text-sm leading-relaxed text-neutral-700">
        <p>
          Before an insurance company will pay you for seeing its members, it
          has to verify who you are: your licence, your training, your
          malpractice cover, your work history. Then it has to offer you a
          contract with a fee schedule. Those are two separate steps, and being
          through the first does not mean you are through the second.
        </p>
        <p>
          It is slow — commonly two to six months per payer — and most of that
          is waiting rather than working. The parts that stall it are almost
          always the same: a question answered inconsistently across
          applications, a gap in your work history nobody explained, or a
          request from a reviewer that arrived by email and sat unanswered past
          its window.
        </p>
        <p>
          So we ask you for everything once, here, and reuse it for every payer
          afterwards. About twenty questions and a handful of documents. You do
          not have to finish in one sitting, and you do not have to finish at
          all — the first part is worth having whether or not you ever apply to
          a panel.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3 border-t border-neutral-200 pt-5">
        <Button type="button" onClick={onStart}>
          Start
        </Button>
        <span className="text-sm text-neutral-500">
          Already on panels?
        </span>
        <Link
          href={INSURANCE_PAYERS_SETTINGS_PATH}
          className="text-sm font-medium text-neutral-900 underline underline-offset-4 hover:text-neutral-700"
        >
          Tell us which ones
        </Link>
      </div>
      <p className="text-xs text-neutral-500">
        Recording the payers you are already contracted with is what lets us
        bill them as claims rather than handing your client a superbill. It
        takes a minute and it is worth doing even if you skip everything else.
      </p>
    </div>
  )
}
