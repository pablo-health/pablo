// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * The invitation page: who invited the reader, a sign-in if they need one,
 * and the button that joins their account to the practice.
 *
 * The token arrives in the URL fragment. It is moved to session storage and
 * stripped from the address bar on arrival, so it survives the round trip
 * through /login (which comes back here by `returnTo`) without sitting in
 * browser history.
 */

import { useEffect, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useQueryClient } from "@tanstack/react-query"
import { useAuth } from "@/lib/auth-context"
import { ApiError } from "@/lib/api/client"
import { previewInvite, redeemInvite } from "@/lib/api/invites"
import { signOutAndClear } from "@/lib/auth/signOutAndClear"

export const INVITE_TOKEN_KEY = "pablo-invite-token"
const SIGN_IN_HERE = "/login?returnTo=%2Finvite"

/** The token from the link, moved out of the address bar; or the one kept from before sign-in. */
function takeToken(): string | null {
  const fromLink = window.location.hash.slice(1)
  if (fromLink) {
    sessionStorage.setItem(INVITE_TOKEN_KEY, fromLink)
    window.history.replaceState(null, "", window.location.pathname)
    return fromLink
  }
  return sessionStorage.getItem(INVITE_TOKEN_KEY)
}

type View =
  | { kind: "loading" }
  | { kind: "failed" }
  | { kind: "broken" }
  | { kind: "gone" }
  | { kind: "open"; practiceName: string; invitedEmail: string }
  | { kind: "mismatch"; practiceName: string; invitedEmail: string; accountEmail: string }
  | { kind: "taken"; practiceName: string }

// Server messages for these are already written for the reader.
const SHOWN_AS_SENT = new Set(["ACCOUNT_CONFLICT", "USER_DISABLED", "EMAIL_NOT_VERIFIED"])
const GENERIC_ERROR = "Something went wrong. Try again."

export function InviteAcceptance() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const { user, loading: authLoading } = useAuth()
  const [token, setToken] = useState<string | null>(null)
  const [view, setView] = useState<View>({ kind: "loading" })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    const found = takeToken()
    void (async () => {
      if (!found) {
        setView({ kind: "broken" })
        return
      }
      setToken(found)
      try {
        const invite = await previewInvite(found)
        setView({ kind: "open", practiceName: invite.practice_name, invitedEmail: invite.email })
      } catch (err) {
        setView(unusableView(err) ?? { kind: "failed" })
      }
    })()
  }, [])

  const practiceName = "practiceName" in view ? view.practiceName : ""

  const join = async (confirmMismatch: boolean) => {
    if (!token) return
    setError("")
    setBusy(true)
    try {
      await redeemInvite(token, confirmMismatch)
      sessionStorage.removeItem(INVITE_TOKEN_KEY)
      router.replace("/dashboard")
    } catch (err) {
      setBusy(false)
      const unusable = unusableView(err)
      if (unusable) {
        setView(unusable)
      } else if (err instanceof ApiError && err.code === "EMAIL_MISMATCH") {
        setView({
          kind: "mismatch",
          practiceName,
          invitedEmail: String(err.details?.invited_email ?? ""),
          accountEmail: String(err.details?.account_email ?? ""),
        })
      } else if (err instanceof ApiError && err.code === "ALREADY_IN_PRACTICE") {
        setView({ kind: "taken", practiceName })
      } else {
        setError(err instanceof ApiError && SHOWN_AS_SENT.has(err.code) ? err.message : GENERIC_ERROR)
      }
    }
  }

  // The token stays in session storage, so /login brings the reader back here.
  const signInAgain = () => signOutAndClear(queryClient, router, SIGN_IN_HERE)

  return (
    <div className="card space-y-5 p-8 text-center">
      <Body
        view={view}
        signedIn={!authLoading && user !== null}
        authLoading={authLoading}
        busy={busy}
        onJoin={join}
        onSignInAgain={signInAgain}
      />
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
    </div>
  )
}

/** The view for an invitation that cannot be accepted at all, or null. */
function unusableView(err: unknown): View | null {
  if (!(err instanceof ApiError)) return null
  if (err.status === 410) {
    sessionStorage.removeItem(INVITE_TOKEN_KEY)
    return { kind: "gone" }
  }
  if (err.status === 404 || err.status === 422) {
    sessionStorage.removeItem(INVITE_TOKEN_KEY)
    return { kind: "broken" }
  }
  return null
}

function Heading({ children }: { children: React.ReactNode }) {
  return <h1 className="font-display text-2xl font-semibold text-neutral-900">{children}</h1>
}

function Text({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-neutral-600">{children}</p>
}

const SECONDARY = "text-sm text-neutral-500 underline hover:text-neutral-700"

function Body({
  view,
  signedIn,
  authLoading,
  busy,
  onJoin,
  onSignInAgain,
}: {
  view: View
  signedIn: boolean
  authLoading: boolean
  busy: boolean
  onJoin: (confirmMismatch: boolean) => void
  onSignInAgain: () => void
}) {
  switch (view.kind) {
    case "loading":
      return <Text>Opening your invitation…</Text>
    case "failed":
      return <Text>We couldn&rsquo;t open your invitation. Reload the page to try again.</Text>
    case "broken":
      return (
        <>
          <Heading>This invitation link doesn&rsquo;t work</Heading>
          <Text>Open the link from your invitation email again.</Text>
        </>
      )
    case "gone":
      return (
        <>
          <Heading>This invitation can&rsquo;t be used</Heading>
          <Text>
            It has already been accepted, was withdrawn, or has expired. Ask the person who invited
            you to send a new one.
          </Text>
        </>
      )
    case "open":
      return (
        <>
          <Heading>You&rsquo;ve been invited to join {view.practiceName}</Heading>
          <Text>The invitation was sent to {view.invitedEmail}.</Text>
          {authLoading ? null : signedIn ? (
            <button
              type="button"
              onClick={() => onJoin(false)}
              disabled={busy}
              className="auth-primary"
            >
              {busy ? "Joining…" : `Join ${view.practiceName}`}
            </button>
          ) : (
            <Link href={SIGN_IN_HERE} className="auth-primary block">
              Sign in to accept
            </Link>
          )}
        </>
      )
    case "mismatch":
      return (
        <>
          <Heading>Which account should join {view.practiceName}?</Heading>
          <Text>
            The invitation was sent to {view.invitedEmail}, and you&rsquo;re signed in as{" "}
            {view.accountEmail}.
          </Text>
          <button
            type="button"
            onClick={() => onJoin(true)}
            disabled={busy}
            className="auth-primary"
          >
            {busy ? "Joining…" : `Join as ${view.accountEmail}`}
          </button>
          <button type="button" onClick={onSignInAgain} disabled={busy} className={SECONDARY}>
            Sign in as {view.invitedEmail} instead
          </button>
        </>
      )
    case "taken":
      return (
        <>
          <Heading>This account already belongs to a practice</Heading>
          <Text>To join {view.practiceName}, sign in with a different account.</Text>
          <button type="button" onClick={onSignInAgain} className={SECONDARY}>
            Sign in with a different account
          </button>
        </>
      )
  }
}
