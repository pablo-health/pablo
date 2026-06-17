// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Recovery-code sign-in for a passkey account whose passkey is unavailable
 * (lost/replaced device). A recovery code is the SECOND factor, so the user
 * still proves a FIRST factor here — their email + password — then spends a
 * one-time code:
 *
 *   1. `signInWithEmailAndPassword` establishes a first-factor session.
 *   2. `redeemRecoveryCode` consumes the code on that session and returns a
 *      custom token carrying the `recovery` factor claim.
 *   3. `signInWithCustomToken` + a forced ID-token refresh upgrade the session
 *      to MFA-satisfied, so it reaches protected data without a second prompt.
 *
 * Recovery codes are issued only at passkey enrollment, so this path is for
 * passkey accounts. An account secured by an authenticator app has no codes
 * and signing in throws `mfa-required` — we say so plainly and send them back.
 */

import { useState } from "react"
import Image from "next/image"
import {
  signInWithEmailAndPassword,
  signInWithCustomToken,
  type UserCredential,
} from "firebase/auth"
import { getFirebaseAuth } from "@/lib/firebase"
import { ApiError } from "@/lib/api/client"
import { redeemRecoveryCode } from "@/lib/api/passkey"
import { firebaseAuthErrorOutcome } from "@/lib/auth-errors"
import { AuthCard } from "./AuthCard"
import { AuthHeader } from "./AuthHeader"
import { AuthInput } from "./AuthInput"
import { AuthFeedback } from "./AuthFeedback"
import { AuthPrimaryButton } from "./AuthPrimaryButton"
import { AuthLinkButton } from "./AuthLinkButton"

interface RecoveryCodeScreenProps {
  initialEmail?: string
  onSuccess: (credential: UserCredential) => void | Promise<void>
  onCancel: () => void
}

export function RecoveryCodeScreen({
  initialEmail = "",
  onSuccess,
  onCancel,
}: RecoveryCodeScreenProps) {
  const [email, setEmail] = useState(initialEmail)
  const [password, setPassword] = useState("")
  const [code, setCode] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")
    setLoading(true)

    const auth = getFirebaseAuth()
    try {
      // First factor: prove the password. A passkey isn't a Firebase-native
      // factor, so for a passkey account this yields a first-factor session
      // (no MFA challenge); an authenticator-app account throws mfa-required.
      await signInWithEmailAndPassword(auth, email, password)

      // Second factor: spend the one-time code on that session.
      let customToken: string
      try {
        const result = await redeemRecoveryCode(code.trim())
        customToken = result.custom_token
      } catch (err) {
        // Keep the first-factor session so the user can re-enter a code without
        // retyping their password.
        if (err instanceof ApiError && err.code === "INVALID_RECOVERY_CODE") {
          setError("That recovery code isn't valid or has already been used.")
        } else {
          setError("Couldn't verify the recovery code. Please try again.")
        }
        return
      }

      // Upgrade to an MFA-satisfied session before handing back to the wizard.
      const credential = await signInWithCustomToken(auth, customToken)
      await credential.user.getIdToken(true)
      await onSuccess(credential)
    } catch (err) {
      const outcome = firebaseAuthErrorOutcome(err, "sign-in")
      if (outcome.kind === "mfa-required") {
        setError(
          "This account is secured by an authenticator app, which doesn't use " +
            "recovery codes. Go back and enter your authenticator code, or contact support."
        )
      } else if (outcome.kind === "message") {
        setError(outcome.message)
      } else {
        setError("Sign-in failed. Check your email and password, then try again.")
      }
    } finally {
      setLoading(false)
    }
  }

  const handleCancel = async () => {
    // Drop any first-factor session left from a failed code attempt so we
    // return to a clean sign-in state.
    try {
      await getFirebaseAuth().signOut()
    } catch {
      // Best-effort — nothing to clean up if there's no session.
    }
    onCancel()
  }

  return (
    <AuthCard>
      <div className="mb-7 flex items-center justify-center gap-2.5">
        <Image src="/pablo-login.webp" alt="" width={44} height={44} className="object-contain" />
        <span className="font-display text-2xl font-bold text-primary-600">Pablo</span>
      </div>

      <AuthHeader
        title="Use a recovery code"
        titleColor="foreground"
        subtitle="Sign in with your email and password, then enter one of the recovery codes you saved when you set up your passkey."
      />

      <form onSubmit={handleSubmit} className="space-y-4">
        <AuthInput
          id="recovery-email"
          label="Email"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          required
        />

        <AuthInput
          id="recovery-password"
          label="Password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          required
        />

        <AuthInput
          id="recovery-code"
          label="Recovery code"
          type="text"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder="ABCDE-FGHJK"
          className="w-full px-4 py-3 border border-neutral-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent text-center font-mono tracking-widest"
          autoComplete="one-time-code"
          autoCapitalize="characters"
          spellCheck={false}
          required
        />

        {error && <AuthFeedback variant="error">{error}</AuthFeedback>}

        <AuthPrimaryButton type="submit" disabled={loading || !email || !password || !code.trim()}>
          {loading ? "Verifying..." : "Sign in"}
        </AuthPrimaryButton>

        <AuthLinkButton size="sm" block onClick={handleCancel}>
          Back to sign in
        </AuthLinkButton>
      </form>
    </AuthCard>
  )
}
