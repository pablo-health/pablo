// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Recovery-code sign-in for a passkey account whose passkey is unavailable
 * (lost/replaced device). A recovery code is the SECOND factor, so the user
 * still proves a FIRST factor here — Google or their email + password — then
 * spends a one-time code:
 *
 *   1. A first factor (Google popup, or email + password) establishes a
 *      first-factor Firebase session.
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
  GoogleAuthProvider,
  signInWithEmailAndPassword,
  signInWithPopup,
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
import { AuthDivider } from "./AuthDivider"
import { AuthFeedback } from "./AuthFeedback"
import { AuthGoogleButton } from "./AuthGoogleButton"
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

  // Once a first factor is established, spend the code and upgrade the session.
  // Returns false (leaving the first-factor session in place so the user can
  // retry the code) when the code itself is rejected.
  const completeRecovery = async (): Promise<void> => {
    let customToken: string
    try {
      const result = await redeemRecoveryCode(code.trim())
      customToken = result.custom_token
    } catch (err) {
      if (err instanceof ApiError && err.code === "INVALID_RECOVERY_CODE") {
        setError("That recovery code isn't valid or has already been used.")
      } else {
        setError("Couldn't verify the recovery code. Please try again.")
      }
      return
    }
    const credential = await signInWithCustomToken(getFirebaseAuth(), customToken)
    await credential.user.getIdToken(true)
    await onSuccess(credential)
  }

  const reportFirstFactorError = (err: unknown, method: "google" | "sign-in") => {
    const outcome = firebaseAuthErrorOutcome(err, method)
    if (outcome.kind === "mfa-required") {
      setError(
        "This account is secured by an authenticator app, which doesn't use " +
          "recovery codes. Go back and enter your authenticator code, or contact support."
      )
    } else if (outcome.kind === "popup-blocked") {
      setError("Your browser blocked the Google sign-in pop-up. Allow pop-ups and try again.")
    } else if (outcome.kind === "message") {
      setError(outcome.message)
    } else {
      setError("Sign-in failed. Please try again.")
    }
  }

  const requireCode = (): boolean => {
    if (code.trim()) return true
    setError("Enter your recovery code first.")
    return false
  }

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!requireCode()) return
    setError("")
    setLoading(true)
    try {
      // A passkey isn't a Firebase-native factor, so for a passkey account this
      // yields a first-factor session; an authenticator-app account throws
      // mfa-required.
      await signInWithEmailAndPassword(getFirebaseAuth(), email, password)
      await completeRecovery()
    } catch (err) {
      reportFirstFactorError(err, "sign-in")
    } finally {
      setLoading(false)
    }
  }

  const handleGoogle = async () => {
    if (loading || !requireCode()) return
    setError("")
    setLoading(true)
    try {
      await signInWithPopup(getFirebaseAuth(), new GoogleAuthProvider())
      await completeRecovery()
    } catch (err) {
      reportFirstFactorError(err, "google")
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
        subtitle="Enter a recovery code you saved when you set up your passkey, then confirm it's you with Google or your password."
      />

      <div className="space-y-4">
        {error && <AuthFeedback variant="error">{error}</AuthFeedback>}

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

        <AuthGoogleButton onClick={handleGoogle} />

        <AuthDivider />

        <form onSubmit={handlePasswordSubmit} className="space-y-4">
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

          <AuthPrimaryButton
            type="submit"
            disabled={loading || !email || !password || !code.trim()}
          >
            {loading ? "Verifying..." : "Sign in"}
          </AuthPrimaryButton>
        </form>

        <AuthLinkButton size="sm" block onClick={handleCancel}>
          Back to sign in
        </AuthLinkButton>
      </div>
    </AuthCard>
  )
}
