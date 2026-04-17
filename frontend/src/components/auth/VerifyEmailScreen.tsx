// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { AuthCard } from "./AuthCard"
import { AuthHeader } from "./AuthHeader"
import { AuthFeedback } from "./AuthFeedback"
import { AuthOutlineButton } from "./AuthOutlineButton"
import { AuthPrimaryButton } from "./AuthPrimaryButton"

interface VerifyEmailScreenProps {
  email: string
  onBack: () => void
  onResend?: () => void | Promise<void>
  resent?: boolean
  error?: string
}

export function VerifyEmailScreen({
  email,
  onBack,
  onResend,
  resent = false,
  error,
}: VerifyEmailScreenProps) {
  return (
    <AuthCard>
      <AuthHeader
        title="Check Your Email"
        subtitle={
          <>
            We sent a verification link to <strong>{email}</strong>. Please
            verify your email before signing in.
          </>
        }
      />

      {error && <AuthFeedback variant="error">{error}</AuthFeedback>}

      {resent && (
        <AuthFeedback variant="success">
          Verification email resent. Check your inbox and spam folder.
        </AuthFeedback>
      )}

      <div className="space-y-3">
        {onResend && (
          <AuthOutlineButton onClick={onResend} disabled={resent}>
            {resent ? "Email Resent" : "Resend Verification Email"}
          </AuthOutlineButton>
        )}

        <AuthPrimaryButton onClick={onBack}>Back to Sign In</AuthPrimaryButton>
      </div>
    </AuthCard>
  )
}
