// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Lets a signed-in clinician with an unverified email send themselves a new
 * verification link, and continue once they have used it.
 *
 * Continuing reloads the user, force-refreshes the ID token so it carries
 * ``email_verified: true``, and re-seeds the server session cookie with it —
 * the dashboard gate reads the cookie, and the API reads the token.
 */

import { useState } from "react"
import { useRouter } from "next/navigation"
import { sendEmailVerification } from "firebase/auth"
import { MailCheck } from "lucide-react"
import { useQueryClient } from "@tanstack/react-query"
import { getFirebaseAuth } from "@/lib/firebase"
import { firebaseAuthErrorOutcome } from "@/lib/auth-errors"
import { errorCode } from "@/lib/errors/errorCode"
import { signOutAndClear } from "@/lib/auth/signOutAndClear"

export function VerifyEmailForm({ email }: { email: string }) {
  const router = useRouter()
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState<"continue" | "resend" | null>(null)
  const [error, setError] = useState("")
  const [resent, setResent] = useState(false)

  const currentUser = async () => {
    const auth = getFirebaseAuth()
    await auth.authStateReady()
    return auth.currentUser
  }

  const handleContinue = async () => {
    if (busy) return
    setBusy("continue")
    setError("")
    try {
      const user = await currentUser()
      if (!user) {
        await signOutAndClear(queryClient, router, "/login")
        return
      }
      await user.reload()
      if (!user.emailVerified) {
        setError("This email isn’t verified yet. Open the link in the email, then try again.")
        return
      }
      const idToken = await user.getIdToken(true)
      await fetch("/api/login", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${idToken}`,
          "X-Refresh-Token": user.refreshToken,
        },
      })
      queryClient.clear()
      router.push("/dashboard")
    } catch (err) {
      console.error("verify-email continue failed:", errorCode(err))
      setError("That didn’t work. Try again.")
    } finally {
      setBusy(null)
    }
  }

  const handleResend = async () => {
    if (busy) return
    setBusy("resend")
    setError("")
    try {
      const user = await currentUser()
      if (!user) {
        await signOutAndClear(queryClient, router, "/login")
        return
      }
      await sendEmailVerification(user, { url: `${window.location.origin}/dashboard` })
      setResent(true)
    } catch (err) {
      console.error("sendEmailVerification failed:", errorCode(err))
      const outcome = firebaseAuthErrorOutcome(err, "verify-email")
      setError(outcome.kind === "message" ? outcome.message : "That didn’t work. Try again.")
    } finally {
      setBusy(null)
    }
  }

  const handleSignOut = async () => {
    await signOutAndClear(queryClient, router, "/login")
  }

  return (
    <div className="card space-y-5 p-8 text-center">
      <MailCheck className="mx-auto h-10 w-10 text-primary-600" />

      <div className="space-y-2">
        <h1 className="font-display text-2xl font-semibold text-neutral-900">
          Verify your email
        </h1>
        <p className="text-sm text-neutral-600">
          {email ? (
            <>
              Verify <strong>{email}</strong> to reach your practice. Use the link in the
              verification email, or send a new one.
            </>
          ) : (
            "Verify your email to reach your practice. Use the link in the verification email, or send a new one."
          )}
        </p>
      </div>

      <button type="button" onClick={handleContinue} className="auth-primary">
        {busy === "continue" ? "Checking…" : "I’ve verified it"}
      </button>

      <button
        type="button"
        onClick={handleResend}
        className="w-full rounded-lg border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
      >
        {busy === "resend" ? "Sending…" : resent ? "Send another link" : "Send a new link"}
      </button>

      {resent && !error && (
        <p role="status" className="text-sm text-green-700">
          Sent. Check your inbox and spam folder.
        </p>
      )}

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
