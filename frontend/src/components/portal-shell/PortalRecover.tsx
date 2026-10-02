// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Asking for a fresh sign-in link — `/portal/{slug}/recover`.
 *
 * The same sign-in the portal's landing shows (`./EmailSignIn`), on a page of
 * its own: links already sent, and the "get a new sign-in link" link beside a
 * code that did not work, point here. Everything about what the page may say
 * after a submission lives in `EmailSignIn`.
 */

"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { resolvePortalPractice } from "@/lib/portal-shell/api"
import { EmailSignIn } from "./EmailSignIn"
import { PortalFooter } from "./PortalFooter"
import { ShellHeader } from "./PortalNav"
import { PortalWelcome } from "./PortalWelcome"

export function PortalRecover({ slug }: { slug: string }) {
  const [displayName, setDisplayName] = useState<string | null>(null)
  const [captchaSiteKey, setCaptchaSiteKey] = useState<string | null>(null)
  const base = `/portal/${encodeURIComponent(slug)}`

  useEffect(() => {
    let cancelled = false
    // One unauthenticated call, carrying both things this page needs before
    // it can ask anybody for an address: the practice's own name, and whether
    // this deployment renders a CAPTCHA widget.
    void resolvePortalPractice(slug).then((result) => {
      if (cancelled || !result.ok) return
      setDisplayName(result.data.display_name)
      setCaptchaSiteKey(result.data.captcha_site_key ?? null)
    })
    return () => {
      cancelled = true
    }
  }, [slug])

  return (
    <div className="flex min-h-screen flex-col bg-neutral-50">
      <ShellHeader displayName={displayName} slots={[]} base={base} section={null} signingOut={false} />

      <main className="flex flex-1 items-start justify-center px-4 py-10 sm:py-16">
        <div className="w-full max-w-md">
          <PortalWelcome displayName={displayName} />
          <EmailSignIn slug={slug} captchaSiteKey={captchaSiteKey} testId="portal-recover-card" />
          <Link
            href={base}
            data-testid="portal-recover-back"
            className="mt-6 block text-center text-sm text-neutral-600 underline underline-offset-4"
          >
            Back
          </Link>
        </div>
      </main>

      <PortalFooter displayName={displayName} />
    </div>
  )
}
