// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import {
  TotpMultiFactorGenerator,
  type MultiFactorResolver,
  type UserCredential,
} from "firebase/auth"
import { firebaseAuthErrorOutcome } from "@/lib/auth-errors"
import { AuthCard } from "./AuthCard"
import { AuthHeader } from "./AuthHeader"
import { AuthInput } from "./AuthInput"
import { AuthFeedback } from "./AuthFeedback"
import { AuthPrimaryButton } from "./AuthPrimaryButton"
import { AuthLinkButton } from "./AuthLinkButton"

interface MfaChallengeScreenProps {
  resolver: MultiFactorResolver
  onSuccess: (credential: UserCredential) => void | Promise<void>
  onCancel: () => void
}

export function MfaChallengeScreen({ resolver, onSuccess, onCancel }: MfaChallengeScreenProps) {
  const [totpCode, setTotpCode] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")
    setLoading(true)

    try {
      const totpHint = resolver.hints.find(
        (hint) => hint.factorId === TotpMultiFactorGenerator.FACTOR_ID
      )

      if (!totpHint) {
        setError("No TOTP factor found. Please contact support.")
        return
      }

      const assertion = TotpMultiFactorGenerator.assertionForSignIn(totpHint.uid, totpCode)
      const credential = await resolver.resolveSignIn(assertion)
      await onSuccess(credential)
    } catch (err) {
      const outcome = firebaseAuthErrorOutcome(err, "mfa")
      if (outcome.kind === "message") setError(outcome.message)
      else setError("MFA verification failed. Please try again.")
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthCard>
      <AuthHeader
        title="Two-Factor Authentication"
        subtitle="Enter the code from your authenticator app"
      />
      <form onSubmit={handleVerify} className="space-y-4">
        <AuthInput
          id="totp-code"
          label="Verification Code"
          type="text"
          inputMode="numeric"
          pattern="[0-9]*"
          value={totpCode}
          onChange={(e) =>
            setTotpCode(e.target.value.replace(/\D/g, "").slice(0, 6))
          }
          placeholder="000000"
          className="w-full px-4 py-3 border border-neutral-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent text-center text-2xl font-mono tracking-widest"
          maxLength={6}
          required
          autoComplete="one-time-code"
          autoFocus
        />

        {error && <AuthFeedback variant="error">{error}</AuthFeedback>}

        <AuthPrimaryButton type="submit" disabled={loading || totpCode.length !== 6}>
          {loading ? "Verifying..." : "Verify"}
        </AuthPrimaryButton>

        <AuthLinkButton size="sm" block onClick={onCancel}>
          Back to sign in
        </AuthLinkButton>
      </form>
    </AuthCard>
  )
}
