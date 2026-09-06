// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Asserts an already-enrolled passkey to upgrade a first-factor session in
 * place, then re-seeds the server session cookie so the dashboard gate sees
 * the new token.
 *
 * Same three steps the passwordless button on `/login` already uses —
 * `beginAuthentication` / `startAuthentication` / `finishAuthentication` —
 * followed by `signInWithCustomToken`, because the minted token is what
 * carries the verified `pablo_amr` factor claim. The forced `getIdToken(true)`
 * matters: without it the SDK can hand back the cached pre-step-up token and
 * the gate bounces the user straight back here.
 *
 * Re-seeding via `/api/login` is not optional either. The dashboard layout
 * reads a server session cookie, so upgrading only the client-side Firebase
 * session would leave the server still holding the first-factor token — the
 * user would loop.
 */

import { useState } from "react"
import { useRouter } from "next/navigation"
import { signInWithCustomToken } from "firebase/auth"
import { startAuthentication, WebAuthnError } from "@simplewebauthn/browser"
import { Fingerprint } from "lucide-react"
import { getFirebaseAuth } from "@/lib/firebase"
import { beginAuthentication, finishAuthentication } from "@/lib/api/passkey"
import { signOutAndClear } from "@/lib/auth/signOutAndClear"
import { useQueryClient } from "@tanstack/react-query"

export function PasskeyStepUpForm() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)

  const handleStepUp = async () => {
    setError("")
    setLoading(true)
    try {
      const options = await beginAuthentication()
      const assertion = await startAuthentication({ optionsJSON: options })
      const { custom_token } = await finishAuthentication(assertion)

      const credential = await signInWithCustomToken(getFirebaseAuth(), custom_token)
      const idToken = await credential.user.getIdToken(true)

      await fetch("/api/login", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${idToken}`,
          "X-Refresh-Token": credential.user.refreshToken,
        },
      })

      // The pre-step-up session answered 403 to everything behind the MFA
      // gate. Those refusals are cached; drop them so the dashboard refetches
      // against the upgraded token instead of rendering stale failures.
      queryClient.clear()
      router.push("/dashboard")
    } catch (err) {
      // Dismissing the platform sheet is a choice, not a failure — leave the
      // screen as it was so the button can simply be pressed again.
      if (err instanceof WebAuthnError && err.name === "NotAllowedError") return
      setError("That didn't work. Try again, or sign out and sign in with your passkey.")
    } finally {
      setLoading(false)
    }
  }

  const handleSignOut = async () => {
    await signOutAndClear(queryClient, router, "/login")
  }

  return (
    <div className="card space-y-5 p-8 text-center">
      <Fingerprint className="mx-auto h-10 w-10 text-primary-600" />

      <div className="space-y-2">
        <h1 className="font-display text-2xl font-semibold text-neutral-900">
          Confirm it&rsquo;s you
        </h1>
        <p className="text-sm text-neutral-600">
          You&rsquo;re signed in, but this sign-in didn&rsquo;t use your passkey. Confirm
          it now to reach your practice.
        </p>
      </div>

      <button
        type="button"
        onClick={handleStepUp}
        disabled={loading}
        autoFocus
        className="w-full rounded-lg bg-primary-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-primary-700 disabled:opacity-60"
      >
        {loading ? "Waiting for your passkey…" : "Use passkey"}
      </button>

      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}

      <button
        type="button"
        onClick={handleSignOut}
        className="text-sm text-neutral-500 underline hover:text-neutral-700"
      >
        Sign out
      </button>
    </div>
  )
}
