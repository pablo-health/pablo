// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Asking a practice for a fresh sign-in link — `/portal/{slug}/recover`.
 *
 * One field and one button. The whole screen is shaped by a single fact
 * about the endpoint behind it: it answers the same way whether or not the
 * address belongs to anybody, because whether someone is a patient of a
 * therapy practice is not something this page gets to confirm to whoever
 * typed the address.
 *
 * So the confirmation is conditional in its wording and unconditional in
 * its behaviour — "if we find a portal account for this email" — and it is
 * shown for every address. There is no error state for "no such patient",
 * because the page never learns that; `ok: false` means the request did not
 * get through at all.
 *
 * CAPTCHA, where the deployment has one configured, is rendered exactly the
 * way the public booking page does it: the same script, the same widget,
 * and the token on the same `X-Captcha-Token` header. A deployment with no
 * provider renders no widget and the endpoint's rate limits are the floor.
 */

"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { requestPortalRecovery, resolvePortalPractice } from "@/lib/portal-shell/api"

const TURNSTILE_SCRIPT_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js"

/**
 * The one thing this page says after a submission, whatever happened.
 *
 * Conditional by design. Saying "we've sent you a link" would confirm the
 * address is on this practice's patient list; saying "no account found"
 * would confirm the opposite. This says what the person needs to do next
 * and nothing about who they are.
 */
const SENT_MESSAGE =
  "If we find a portal account for this email, we'll send a new sign-in link."

/** Shown only when the request never reached the server, or was turned away. */
const FAILED_MESSAGE = "We couldn't send that just now. Wait a minute and try again."

export function PortalRecover({ slug }: { slug: string }) {
  const [displayName, setDisplayName] = useState<string | null>(null)
  const [captchaSiteKey, setCaptchaSiteKey] = useState<string | null>(null)
  const [captchaToken, setCaptchaToken] = useState<string | null>(null)
  const [email, setEmail] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [sent, setSent] = useState(false)
  const [failed, setFailed] = useState(false)
  const captchaContainerRef = useRef<HTMLDivElement | null>(null)
  const captchaWidgetIdRef = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false
    // One unauthenticated call, carrying both things this page needs before
    // it can ask anybody for an address: the practice's own name for the
    // header, and whether this deployment renders a CAPTCHA widget.
    void resolvePortalPractice(slug).then((result) => {
      if (cancelled || !result.ok) return
      setDisplayName(result.data.display_name)
      setCaptchaSiteKey(result.data.captcha_site_key ?? null)
    })
    return () => {
      cancelled = true
    }
  }, [slug])

  useEffect(() => {
    if (!captchaSiteKey) return
    const render = () => {
      if (!captchaContainerRef.current || !window.turnstile) return
      captchaWidgetIdRef.current = window.turnstile.render(captchaContainerRef.current, {
        sitekey: captchaSiteKey,
        callback: (token: string) => setCaptchaToken(token),
      })
    }
    if (window.turnstile) {
      render()
      return
    }
    const script = document.createElement("script")
    script.src = TURNSTILE_SCRIPT_SRC
    script.async = true
    script.onload = render
    document.head.appendChild(script)
  }, [captchaSiteKey])

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (!email.trim() || submitting) return
    setSubmitting(true)
    setFailed(false)
    const result = await requestPortalRecovery(slug, email.trim(), captchaToken)
    setSubmitting(false)
    if (result.ok) {
      setSent(true)
    } else {
      setFailed(true)
      if (captchaWidgetIdRef.current) window.turnstile?.reset(captchaWidgetIdRef.current)
      setCaptchaToken(null)
    }
  }

  const canSubmit =
    email.trim().length > 0 && !submitting && (captchaSiteKey === null || captchaToken !== null)

  return (
    <div className="flex min-h-screen flex-col bg-neutral-50">
      <header className="border-b border-neutral-200 bg-white px-4 py-4">
        <div className="mx-auto max-w-md">
          {displayName ? (
            <h1 data-testid="portal-recover-practice-name" className="text-base font-semibold">
              {displayName}
            </h1>
          ) : (
            <div className="h-5 w-40 animate-pulse rounded bg-neutral-200" aria-hidden="true" />
          )}
        </div>
      </header>

      <main className="flex flex-1 items-start justify-center px-4 py-8 sm:py-12">
        <div className="w-full max-w-md">
          <div
            data-testid="portal-recover-card"
            className="rounded-lg border border-neutral-200 bg-white p-6 shadow-sm"
          >
            <h2 className="text-base font-semibold text-neutral-900">Get a new sign-in link</h2>
            <p className="mt-1 text-sm text-neutral-600">
              Enter the email address your practice has for you.
            </p>

            <form onSubmit={handleSubmit} className="mt-4">
              <Label htmlFor="portal-recover-email">Email</Label>
              <Input
                id="portal-recover-email"
                data-testid="portal-recover-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                className="mt-1"
              />
              {captchaSiteKey && <div ref={captchaContainerRef} className="mt-4" />}
              <Button
                type="submit"
                data-testid="portal-recover-submit"
                disabled={!canSubmit}
                className="mt-4 w-full"
                size="lg"
              >
                {submitting ? "Sending…" : "Send link"}
              </Button>
            </form>

            {/* Announced rather than merely rendered: the person who just
                submitted may not be looking at this corner of the page. */}
            <div aria-live="polite" role="status">
              {sent && (
                <p data-testid="portal-recover-sent" className="mt-4 text-sm text-neutral-600">
                  {SENT_MESSAGE}
                </p>
              )}
              {failed && (
                <p data-testid="portal-recover-failed" className="mt-4 text-sm text-red-600">
                  {FAILED_MESSAGE}
                </p>
              )}
            </div>

            <Link
              href={`/portal/${encodeURIComponent(slug)}`}
              data-testid="portal-recover-back"
              className="mt-6 block text-sm text-neutral-600 underline underline-offset-4"
            >
              Back
            </Link>
          </div>
        </div>
      </main>

      <footer className="px-4 py-6 text-center text-xs text-neutral-400">
        <p>Powered by Pablo</p>
      </footer>
    </div>
  )
}
