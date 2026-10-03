// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Signing in from the portal's front door: an email address, and a link sent
 * to it. The portal's landing (`PortalShell` with no session) and the
 * recovery page (`/portal/{slug}/recover`) both show this, against the same
 * recovery endpoint.
 *
 * Three states, and the whole shape is set by one fact about that endpoint:
 * it answers the same way whether or not the address belongs to anybody,
 * because whether someone is a client of a therapy practice is not something
 * this page gets to confirm to whoever typed the address.
 *
 *   sign in        -> the field and the button; with a one-line note when the
 *                     visitor arrived on a link or a session that has expired
 *   check email    -> shown for EVERY address once the request got through,
 *                     worded conditionally ("if we find a portal account")
 *   (not through)  -> the field stays, with a line saying to try again
 *
 * There is no "no such client" state, because the page never learns that.
 *
 * CAPTCHA, where the deployment has one configured, is rendered exactly the
 * way the public booking page does it: the same script, the same widget, and
 * the token on the same `X-Captcha-Token` header. A deployment with no
 * provider renders no widget and the endpoint's rate limits are the floor.
 */

"use client"

import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { requestPortalRecovery } from "@/lib/portal-shell/api"
import { CardShell } from "./PortalAuthCards"

const TURNSTILE_SCRIPT_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js"

/**
 * The one thing said after a submission that got through, whatever address
 * it was. "We've sent you a link" would confirm the address is on this
 * practice's client list; "no account found" would confirm the opposite.
 */
export const SENT_MESSAGE = "If we find a portal account for this email, we'll send a new sign-in link."

/** Shown only when the request never reached the server, or was turned away. */
const FAILED_MESSAGE = "We couldn't send that just now. Wait a minute and try again."

interface EmailSignInProps {
  slug: string
  /** The deployment's CAPTCHA site key, from resolving the practice; `null` for none. */
  captchaSiteKey: string | null
  /** One line above the form, saying why the visitor is here again. */
  note?: { text: string; testId: string }
  testId: string
}

export function EmailSignIn({ slug, captchaSiteKey, note, testId }: EmailSignInProps) {
  const [email, setEmail] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [sent, setSent] = useState(false)
  const [failed, setFailed] = useState(false)
  const [captchaToken, setCaptchaToken] = useState<string | null>(null)
  const captchaContainerRef = useRef<HTMLDivElement | null>(null)
  const captchaWidgetIdRef = useRef<string | null>(null)
  const sentHeadingRef = useRef<HTMLHeadingElement | null>(null)

  useEffect(() => {
    if (!captchaSiteKey || sent) return
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
  }, [captchaSiteKey, sent])

  // The form is gone once the request is through; take focus to what
  // replaced it so a keyboard or screen-reader user is not left nowhere.
  useEffect(() => {
    if (sent) sentHeadingRef.current?.focus()
  }, [sent])

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

  function startOver() {
    setSent(false)
    setEmail("")
    setCaptchaToken(null)
  }

  // The button is the page's main action, so it stays solid and ready; an
  // empty field is caught by the field itself (`required`) and by
  // `handleSubmit`. It waits only on a request in flight or the CAPTCHA.
  const canSubmit = !submitting && (captchaSiteKey === null || captchaToken !== null)

  return (
    <CardShell testId={testId} roomy>
      {/* Announced rather than merely rendered: the person who just submitted
          may not be looking at this corner of the page. */}
      <div aria-live="polite" role="status">
        {sent && (
          <div data-testid="portal-check-email">
            <h2 ref={sentHeadingRef} tabIndex={-1} className="text-xl font-semibold text-neutral-900 outline-none">
              Check your email
            </h2>
            <p data-testid="portal-recover-sent" className="mt-2 text-sm text-neutral-600">
              {SENT_MESSAGE}
            </p>
          </div>
        )}
        {failed && (
          <p data-testid="portal-recover-failed" className="mb-4 text-sm text-red-600">
            {FAILED_MESSAGE}
          </p>
        )}
      </div>

      {sent ? (
        <Button
          data-testid="portal-recover-different-email"
          variant="outline"
          onClick={startOver}
          className="mt-6 w-full"
          size="lg"
        >
          Use a different email
        </Button>
      ) : (
        <>
          {note && (
            <p data-testid={note.testId} className="mb-4 text-sm font-medium text-neutral-800">
              {note.text}
            </p>
          )}
          <h2 className="text-xl font-semibold text-neutral-900">Sign in</h2>
          <p className="mt-2 text-sm text-neutral-600">Enter your email and we&apos;ll send you a link to sign in.</p>

          <form onSubmit={handleSubmit} className="mt-6">
            <Label htmlFor="portal-recover-email">Email</Label>
            <Input
              id="portal-recover-email"
              data-testid="portal-recover-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              required
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
              {submitting ? "Sending…" : "Email me a sign-in link"}
            </Button>
          </form>

          <p className="mt-6 text-sm text-neutral-600">New here? Your practice will send you an invitation.</p>
        </>
      )}
    </CardShell>
  )
}
