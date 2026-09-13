// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { ExternalLink } from "lucide-react"

/**
 * For the therapist who does not have an NPI at all.
 *
 * Not the same person as the one who cannot remember hers, and the old screen
 * conflated them by offering a box to type a number into. Plenty of licensed
 * clinicians genuinely have no NPI: it is required to bill insurance
 * electronically, not to practise, so someone who has only ever taken cash may
 * never have applied. A newly licensed associate often has not either.
 *
 * It is not issued by a licensing board and does not arrive automatically —
 * she applies to CMS herself, free, in about ten minutes. So the honest screen
 * is not an error; it is the next thing to do, with what she will need to hand.
 *
 * Pablo does not apply on her behalf. CMS has a sanctioned route for that (a
 * surrogate relationship, or bulk enumeration as an EFI organisation), but the
 * application carries a certification statement somebody signs, and signing
 * that for a clinician is a legal question rather than an engineering one. Any
 * future version of this screen that files for her goes past counsel first.
 */
export function NoNpiYet() {
  return (
    <div className="rounded-xl border border-stone-200 bg-white p-4">
      <p className="text-sm font-medium text-stone-900">You&rsquo;ll need an NPI</p>
      <p className="mt-1 text-sm text-stone-600">
        It&rsquo;s the number payers identify you by, and you need one to bill
        insurance. It&rsquo;s free, you apply directly to CMS, and it usually
        takes about ten minutes. It is not issued by your licensing board, so
        having a licence doesn&rsquo;t mean you have one.
      </p>

      <p className="mt-3 text-xs font-semibold uppercase tracking-wide text-stone-500">
        What to have ready
      </p>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-stone-600">
        <li>Your Social Security number</li>
        <li>Your licence number and the state that issued it</li>
        <li>Your practice address and phone number</li>
        <li>Your taxonomy &mdash; the specialty code for what you do</li>
      </ul>

      <a
        href="https://nppes.cms.hhs.gov/"
        target="_blank"
        rel="noopener noreferrer"
        className="mt-4 inline-flex items-center gap-1 text-sm font-medium text-stone-900 underline underline-offset-4 hover:text-stone-700"
      >
        Apply at NPPES
        <ExternalLink className="h-3.5 w-3.5" aria-hidden />
      </a>

      <p className="mt-3 text-xs text-stone-500">
        Come back when you have it and we&rsquo;ll pick up here. Nothing else in
        your setup is blocked on it &mdash; you can keep going and see clients
        who pay you directly.
      </p>
    </div>
  )
}
