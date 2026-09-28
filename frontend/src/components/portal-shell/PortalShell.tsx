// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient portal shell — standalone, patient-facing chrome with no
 * clinician nav and no dashboard chrome. The layout of every page at
 * `/portal/{slug}` (Home) and `/portal/{slug}/{section}`, so it stays
 * mounted while the patient moves between them: the session is proved once
 * per visit, not once per page.
 *
 * **The invitation arrives in the URL fragment.** A fragment is never sent
 * to a server, so a link that carries a live credential stays out of access
 * logs, out of `Referer` headers and out of every proxy in between. The
 * shell reads it off `location.hash`, spends it, and takes it back out of
 * the address bar with `history.replaceState` so a shared screen or a
 * reloaded tab is not holding one. Only Home reads it — an invitation link
 * always points there, and the patient lands there once the code is in.
 *
 * State machine, driven entirely off `@/lib/portal-shell/{api,session}`:
 *
 *   resolving   -> skeleton while the slug resolves
 *   unknown     -> the slug doesn't resolve to a practice this deployment
 *                  serves a portal for
 *   no-session  -> no stored session and no invitation
 *   otp         -> no stored session, invitation present: enter the code
 *   active      -> a live (or freshly redeemed) session; renders the page
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

import { useCallback, useEffect, useMemo, useState } from "react"
import { usePathname } from "next/navigation"
import { fetchCapabilities, resolvePortalPractice } from "@/lib/portal-shell/api"
import { portalLocation } from "@/lib/portal-shell/paths"
import { bootstrapSession, redeemAndStore, signOutAndForget } from "@/lib/portal-shell/session"
import { type CapabilitiesState, type PortalView, PortalViewProvider } from "./context"
import {
  NoSessionCard,
  OtpCard,
  ResolvingCard,
  UnknownPracticeCard,
} from "./PortalAuthCards"
import { ShellHeader } from "./PortalNav"
import { visiblePortalSlots } from "./slots"
// Side-effect import: fills the slot registry in the BROWSER's module graph.
// It has to happen from a client component — see modules.tsx.
import "./modules"

type Phase = "resolving" | "unknown" | "no-session" | "otp" | "active" | "expired"

/** The invitation in the URL fragment, if this page was opened with one. */
function invitationInUrl(slug: string): string | null {
  if (typeof window === "undefined") return null
  if (portalLocation(slug, window.location.pathname).section !== null) return null
  const fragment = window.location.hash
  if (!fragment.startsWith("#")) return null
  return new URLSearchParams(fragment.slice(1)).get("invite")
}

/** Leave the practice's address in the bar, with no credential on it. */
function forgetInvitationInUrl(): void {
  if (typeof window === "undefined") return
  window.history.replaceState(null, "", window.location.pathname + window.location.search)
}

export function PortalShell({ slug, children }: { slug: string; children?: React.ReactNode }) {
  const pathname = usePathname() ?? ""
  const { base, section } = portalLocation(slug, pathname)
  const [phase, setPhase] = useState<Phase>("resolving")
  const [displayName, setDisplayName] = useState<string | null>(null)
  const [sessionToken, setSessionToken] = useState<string | null>(null)
  const [invitation, setInvitation] = useState<string | null>(null)
  const [otp, setOtp] = useState("")
  const [otpError, setOtpError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [capabilities, setCapabilities] = useState<CapabilitiesState>({ status: "loading" })
  const [signingOut, setSigningOut] = useState(false)

  useEffect(() => {
    let cancelled = false

    async function load() {
      const token = invitationInUrl(slug)
      setInvitation(token)

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
  // phase renders a page or a navigation to gate.
  useEffect(() => {
    if (phase !== "active" || sessionToken === null) return
    let cancelled = false

    void fetchCapabilities(sessionToken).then((result) => {
      if (cancelled) return
      if (!result.ok) {
        setCapabilities({ status: "failed" })
        return
      }
      setCapabilities({ status: "loaded", data: result.data })
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
    const result = await redeemAndStore(slug, invitation, otp.trim())
    setSubmitting(false)
    if (result.ok) {
      setSessionToken(result.sessionToken)
      forgetInvitationInUrl()
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
    await signOutAndForget(slug, sessionToken)
    setSigningOut(false)
    // Whatever the server said, this browser is no longer holding a
    // session — so the shell shows the signed-out state rather than a
    // screen the patient can no longer act on.
    setSessionToken(null)
    setCapabilities({ status: "loading" })
    setPhase("no-session")
  }, [slug, sessionToken, signingOut])

  const signedIn = phase === "active" && sessionToken !== null
  // Until the document arrives every slot counts as served — see
  // `visiblePortalSlots` for why a missing document keeps everything.
  const modules = capabilities.status === "loaded" ? capabilities.data.modules : null
  const slots = useMemo(() => (signedIn ? visiblePortalSlots(modules) : []), [signedIn, modules])

  const view: PortalView | null =
    signedIn && sessionToken !== null
      ? { slug, sessionToken, capabilities, displayName, slots, base }
      : null

  return (
    <div className="flex min-h-screen flex-col bg-neutral-50">
      <ShellHeader
        displayName={displayName}
        slots={slots}
        base={base}
        section={section}
        onSignOut={signedIn ? handleSignOut : undefined}
        signingOut={signingOut}
      />
      <main className="flex flex-1 items-start justify-center px-4 py-8 sm:py-12">
        <div className="w-full max-w-md">
          {phase === "resolving" && <ResolvingCard />}
          {phase === "unknown" && <UnknownPracticeCard />}
          {phase === "no-session" && <NoSessionCard slug={slug} />}
          {phase === "expired" && <NoSessionCard slug={slug} revoked />}
          {phase === "otp" && (
            <OtpCard
              otp={otp}
              onOtpChange={setOtp}
              onSubmit={handleRedeem}
              submitting={submitting}
              error={otpError}
            />
          )}
          {view !== null && (
            <PortalViewProvider value={view}>
              <div data-testid="portal-shell-active" className="flex flex-col gap-4">
                {children}
              </div>
            </PortalViewProvider>
          )}
        </div>
      </main>
      <footer className="px-4 py-6 text-center text-xs text-neutral-400">
        <p>Powered by Pablo</p>
      </footer>
    </div>
  )
}
