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

import { useEffect, useState } from "react"
import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { resolvePortalPractice } from "@/lib/portal-shell/api"
import { bootstrapSession, redeemAndStore } from "@/lib/portal-shell/session"
import { getPortalSlots, type PortalSlotProps } from "./slots"
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

  return (
    <div className="flex min-h-screen flex-col bg-neutral-50">
      <ShellHeader displayName={displayName} />
      <main className="flex flex-1 items-start justify-center px-4 py-8 sm:py-12">
        <div className="w-full max-w-md">
          {phase === "resolving" && <ResolvingCard />}
          {phase === "unknown" && <UnknownPracticeCard />}
          {phase === "no-session" && <NoSessionCard />}
          {phase === "expired" && <NoSessionCard revoked />}
          {phase === "otp" && (
            <OtpCard
              otp={otp}
              onOtpChange={setOtp}
              onSubmit={handleRedeem}
              submitting={submitting}
              error={otpError}
            />
          )}
          {phase === "active" && sessionToken !== null && (
            <ActiveShellBody slug={practiceSlug} sessionToken={sessionToken} />
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

function ShellHeader({ displayName }: { displayName: string | null }) {
  return (
    <header className="border-b border-neutral-200 bg-white px-4 py-4">
      <div className="mx-auto max-w-md">
        {displayName ? (
          <h1 data-testid="portal-shell-practice-name" className="text-base font-semibold">
            {displayName}
          </h1>
        ) : (
          <div className="h-5 w-40 animate-pulse rounded bg-neutral-200" aria-hidden="true" />
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

function NoSessionCard({ revoked = false }: { revoked?: boolean }) {
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
 * The signed-in body: whatever slots are registered, in registration order.
 *
 * Hands each slot the slug and the live session token, so a slot can call a
 * patient-authenticated route without going looking for the session itself.
 */
function ActiveShellBody({ slug, sessionToken }: PortalSlotProps) {
  const slots = getPortalSlots()
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
          <Component key={id} slug={slug} sessionToken={sessionToken} />
        ))
      )}
    </div>
  )
}
