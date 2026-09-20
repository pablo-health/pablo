// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient portal shell — standalone, patient-facing chrome with no
 * clinician nav and no dashboard chrome. Served at `/portal/{slug}`.
 *
 * **The invitation arrives in the URL fragment.** A fragment is never sent
 * to a server, so a link that carries a live credential stays out of access
 * logs, out of `Referer` headers and out of every proxy in between. The
 * shell reads it off `location.hash`, spends it, and takes it back out of
 * the address bar with `history.replaceState` so a shared screen or a
 * reloaded tab is not holding one.
 *
 * State machine, driven entirely off `@/lib/portal-shell/{api,session}`:
 *
 *   resolving   -> skeleton while the slug resolves
 *   unknown     -> the slug doesn't resolve to a practice this deployment
 *                  serves a portal for
 *   no-session  -> no stored session and no invitation
 *   otp         -> no stored session, invitation present: enter the code
 *   active      -> a live (or freshly redeemed) session; renders slots
 *   expired     -> a stored session's `/refresh` came back 401
 *
 * The invitation is only consulted when there is NO stored session to
 * bootstrap: an expired or revoked session always lands on `expired`, never
 * back on the code form, even if the URL happens to carry a fresh
 * invitation — a stale tab re-using an old link should not silently
 * re-authenticate against state the shell hasn't reloaded.
 *
 * Every redeem failure — wrong code, expired invitation, attempt-capped,
 * already spent — renders the SAME generic, retryable message. The
 * backend's uniform 401 is the whole point, and a friendlier per-cause
 * message here would rebuild the oracle the server refused to be.
 */

"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { fetchCapabilities, resolvePortalPractice } from "@/lib/portal-shell/api"
import { bootstrapSession, redeemAndStore, signOutAndForget } from "@/lib/portal-shell/session"
import { visiblePortalSlots, type PortalSlot, type PortalSlotProps } from "./slots"
// Side-effect import: fills the slot registry in the BROWSER's module graph.
// It has to happen from a client component — see modules.tsx.
import "./modules"

type Phase = "resolving" | "unknown" | "no-session" | "otp" | "active" | "expired"

/**
 * The segment an invitation link used to land on, kept working because
 * links already in inboxes point at it. It names no practice — the redeem
 * response does — so the shell skips straight to the code form and learns
 * whose portal this is from what comes back.
 */
const SLUGLESS_LANDING = "redeem"

/** The invitation in the URL fragment, if this page was opened with one. */
function invitationInUrl(): string | null {
  if (typeof window === "undefined") return null
  const fragment = window.location.hash
  if (!fragment.startsWith("#")) return null
  return new URLSearchParams(fragment.slice(1)).get("token")
}

/** Put the practice's own address in the bar, with no credential on it. */
function forgetInvitationInUrl(practiceSlug: string): void {
  if (typeof window === "undefined") return
  window.history.replaceState(null, "", `/portal/${encodeURIComponent(practiceSlug)}`)
}

export function PortalShell({ slug }: { slug: string }) {
  const [phase, setPhase] = useState<Phase>("resolving")
  const [displayName, setDisplayName] = useState<string | null>(null)
  const [sessionToken, setSessionToken] = useState<string | null>(null)
  const [practiceSlug, setPracticeSlug] = useState(slug)
  const [invitation, setInvitation] = useState<string | null>(null)
  const [otp, setOtp] = useState("")
  const [otpError, setOtpError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  // `null` until the capability document arrives, and `null` again if it
  // never does — which keeps every slot rendered. See `visiblePortalSlots`.
  const [modules, setModules] = useState<Record<string, boolean> | null>(null)
  const [signingOut, setSigningOut] = useState(false)

  useEffect(() => {
    let cancelled = false

    async function load() {
      const token = invitationInUrl()
      setInvitation(token)

      // Nothing to resolve: this path names no practice. With an invitation
      // the code form is the whole page; without one there is nothing here.
      if (slug === SLUGLESS_LANDING) {
        setPhase(token ? "otp" : "unknown")
        return
      }

      const resolved = await resolvePortalPractice(slug)
      if (cancelled) return
      // An invitation does not rescue a slug that resolves to nothing: the
      // generic dead end is the same one every unresolvable address gets.
      if (!resolved.ok) {
        setPhase("unknown")
        return
      }
      setDisplayName(resolved.data.display_name)

      const bootstrap = await bootstrapSession(slug)
      if (cancelled) return
      if (bootstrap.status === "active") {
        setSessionToken(bootstrap.sessionToken)
        setPhase("active")
      } else if (bootstrap.status === "expired") {
        setPhase("expired")
      } else if (token) {
        setPhase("otp")
      } else {
        setPhase("no-session")
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [slug])

  // The capability document is fetched once a session is live, and only
  // then: it is a patient-authenticated call, and nothing before the active
  // phase renders a slot or a navigation to gate.
  useEffect(() => {
    if (phase !== "active" || sessionToken === null) return
    let cancelled = false

    void fetchCapabilities(sessionToken).then((result) => {
      if (cancelled || !result.ok) return
      setModules(result.data.modules)
      // The practice name from the signed-in side, which is the same
      // directory entry the slug resolved through — so the header does not
      // change under the patient when it arrives.
      if (result.data.practice.display_name) {
        setDisplayName(result.data.practice.display_name)
      }
    })

    return () => {
      cancelled = true
    }
  }, [phase, sessionToken])

  async function handleRedeem() {
    if (!invitation || !otp.trim() || submitting) return
    setSubmitting(true)
    setOtpError(null)
    const result = await redeemAndStore(invitation, otp.trim())
    setSubmitting(false)
    if (result.ok) {
      setSessionToken(result.session.sessionToken)
      setPracticeSlug(result.session.practiceSlug)
      setDisplayName(result.session.practiceDisplayName)
      forgetInvitationInUrl(result.session.practiceSlug)
      setPhase("active")
    } else {
      setOtpError(
        "That code didn't work. Check it and try again, or ask your practice for a new invite link.",
      )
    }
  }

  const handleSignOut = useCallback(async () => {
    if (sessionToken === null || signingOut) return
    setSigningOut(true)
    await signOutAndForget(practiceSlug, sessionToken)
    setSigningOut(false)
    // Whatever the server said, this browser is no longer holding a
    // session — so the shell shows the signed-out state rather than a
    // screen the patient can no longer act on.
    setSessionToken(null)
    setModules(null)
    setPhase("no-session")
  }, [practiceSlug, sessionToken, signingOut])

  const signedIn = phase === "active" && sessionToken !== null
  const slots = signedIn ? visiblePortalSlots(modules) : []

  return (
    <div className="flex min-h-screen flex-col bg-neutral-50">
      <ShellHeader
        displayName={displayName}
        slots={slots}
        onSignOut={signedIn ? handleSignOut : undefined}
        signingOut={signingOut}
      />
      <main className="flex flex-1 items-start justify-center px-4 py-8 sm:py-12">
        <div className="w-full max-w-md">
          {phase === "resolving" && <ResolvingCard />}
          {phase === "unknown" && <UnknownPracticeCard />}
          {phase === "no-session" && <NoSessionCard slug={practiceSlug} />}
          {phase === "expired" && <NoSessionCard slug={practiceSlug} revoked />}
          {phase === "otp" && (
            <OtpCard
              otp={otp}
              onOtpChange={setOtp}
              onSubmit={handleRedeem}
              submitting={submitting}
              error={otpError}
            />
          )}
          {signedIn && sessionToken !== null && (
            <ActiveShellBody slug={practiceSlug} sessionToken={sessionToken} slots={slots} />
          )}
        </div>
      </main>
      <ShellFooter />
    </div>
  )
}

function CardShell({
  children,
  testId,
}: {
  children: React.ReactNode
  testId: string
}) {
  return (
    <div
      data-testid={testId}
      className="rounded-lg border border-neutral-200 bg-white p-6 shadow-sm"
    >
      {children}
    </div>
  )
}

function ShellHeader({
  displayName,
  slots,
  onSignOut,
  signingOut,
}: {
  displayName: string | null
  slots: PortalSlot[]
  onSignOut?: () => void
  signingOut: boolean
}) {
  // Only slots that asked for a label appear in the navigation; a slot
  // without one still renders in the body.
  const navSlots = slots.filter((slot) => slot.label !== undefined)
  return (
    <header className="border-b border-neutral-200 bg-white px-4 py-4">
      <div className="mx-auto flex max-w-md flex-wrap items-center justify-between gap-x-4 gap-y-2">
        {displayName ? (
          <h1 data-testid="portal-shell-practice-name" className="text-base font-semibold">
            {displayName}
          </h1>
        ) : (
          <div className="h-5 w-40 animate-pulse rounded bg-neutral-200" aria-hidden="true" />
        )}
        {onSignOut && (
          <Button
            data-testid="portal-shell-sign-out"
            onClick={onSignOut}
            disabled={signingOut}
            variant="ghost"
            size="sm"
          >
            {signingOut ? "Signing out…" : "Sign out"}
          </Button>
        )}
        {navSlots.length > 0 && (
          <nav
            data-testid="portal-shell-nav"
            aria-label="Portal sections"
            className="w-full border-t border-neutral-100 pt-2"
          >
            <ul className="flex flex-wrap gap-x-4 gap-y-1">
              {navSlots.map((slot) => (
                <li key={slot.id}>
                  {/* An in-page anchor rather than a route: v1 renders every
                      section on one page, so the navigation moves the
                      viewport instead of fetching. Keeping it a real link
                      means it is reachable by keyboard and shareable. */}
                  <a
                    href={`#portal-section-${slot.id}`}
                    data-testid={`portal-shell-nav-${slot.id}`}
                    className="text-sm text-neutral-600 underline-offset-4 hover:underline focus-visible:underline"
                  >
                    {slot.label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        )}
      </div>
    </header>
  )
}

function ShellFooter() {
  return (
    <footer className="px-4 py-6 text-center text-xs text-neutral-400">
      <p>Powered by Pablo</p>
    </footer>
  )
}

function ResolvingCard() {
  return (
    <CardShell testId="portal-shell-skeleton">
      <div className="flex flex-col items-center gap-3 py-8 text-neutral-500">
        <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
        <p className="text-sm">Loading…</p>
      </div>
    </CardShell>
  )
}

function UnknownPracticeCard() {
  return (
    <CardShell testId="portal-shell-unknown">
      <div className="flex flex-col items-center gap-2 py-4 text-center">
        <h2 className="text-base font-semibold text-neutral-900">This link isn&apos;t right</h2>
        <p className="text-sm text-neutral-600">
          Double check the address, or ask your practice to send you a new link.
        </p>
      </div>
    </CardShell>
  )
}

function NoSessionCard({ slug, revoked = false }: { slug: string; revoked?: boolean }) {
  return (
    <CardShell testId="portal-shell-no-session">
      <div className="flex flex-col items-center gap-2 py-4 text-center">
        <h2 className="text-base font-semibold text-neutral-900">Check your email</h2>
        <p className="text-sm text-neutral-600">
          Look for your invite link — or ask your practice to send one.
        </p>
        {revoked && (
          <p data-testid="portal-shell-expired-note" className="mt-2 text-sm text-neutral-500">
            Your access has ended. Ask your practice to send a new invite link when you&apos;re
            ready to continue.
          </p>
        )}
        {/* Offered on both, because the shell cannot tell a lapsed session
            from a withdrawn one and neither can the recovery page — it
            answers the same way either way. */}
        <Link
          href={`/portal/${encodeURIComponent(slug)}/recover`}
          data-testid="portal-shell-recover-link"
          className="mt-2 text-sm text-neutral-600 underline underline-offset-4"
        >
          Send me a new link
        </Link>
      </div>
    </CardShell>
  )
}

function OtpCard({
  otp,
  onOtpChange,
  onSubmit,
  submitting,
  error,
}: {
  otp: string
  onOtpChange: (value: string) => void
  onSubmit: () => void
  submitting: boolean
  error: string | null
}) {
  const canSubmit = otp.trim().length > 0 && !submitting
  return (
    <CardShell testId="portal-shell-otp">
      <h2 className="text-base font-semibold text-neutral-900">Enter your code</h2>
      <p className="mt-1 text-sm text-neutral-600">
        We sent a code by text message. Enter it below to continue.
      </p>
      <div className="mt-4">
        <Label htmlFor="portal-otp">Code</Label>
        <Input
          id="portal-otp"
          data-testid="portal-shell-otp-input"
          value={otp}
          onChange={(e) => onOtpChange(e.target.value)}
          inputMode="numeric"
          autoComplete="one-time-code"
          className="mt-1"
        />
      </div>
      {error && (
        <p data-testid="portal-shell-otp-error" className="mt-3 text-sm text-red-600">
          {error}
        </p>
      )}
      <Button
        data-testid="portal-shell-otp-submit"
        onClick={onSubmit}
        disabled={!canSubmit}
        className="mt-4 w-full"
        size="lg"
      >
        {submitting ? "Checking…" : "Continue"}
      </Button>
    </CardShell>
  )
}

/**
 * The signed-in body: the slots this deployment serves, in registration
 * order.
 *
 * Hands each slot the slug and the live session token, so a slot can call a
 * patient-authenticated route without going looking for the session itself.
 *
 * The empty state covers two cases that look the same to the patient and
 * should: nothing is registered, and nothing this practice has turned on is
 * registered. Neither is an error, and saying which would be describing the
 * deployment to somebody who cannot act on it.
 */
function ActiveShellBody({
  slug,
  sessionToken,
  slots,
}: PortalSlotProps & { slots: PortalSlot[] }) {
  return (
    <div data-testid="portal-shell-active" className="flex flex-col gap-4">
      {slots.length === 0 ? (
        <CardShell testId="portal-shell-empty">
          <p className="py-4 text-center text-sm text-neutral-600">
            Nothing here yet — your practice will send you anything they need.
          </p>
        </CardShell>
      ) : (
        slots.map(({ id, Component }) => (
          <section key={id} id={`portal-section-${id}`}>
            <Component slug={slug} sessionToken={sessionToken} />
          </section>
        ))
      )}
    </div>
  )
}
