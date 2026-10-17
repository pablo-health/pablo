// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Firebase implementation of the `/auth/action` surface — the handler for
 * Firebase email-link actions (verify email, reset password, recover
 * email, revert second-factor). Firebase emails link here with a
 * ``mode`` + ``oobCode``. Other providers host their own action pages, so
 * their surface is a redirect shell instead.
 */

import { useEffect, useRef, useState, Suspense } from "react"
import { useSearchParams } from "next/navigation"
import {
  applyActionCode,
  checkActionCode,
  confirmPasswordReset,
  verifyPasswordResetCode,
} from "firebase/auth"
import { getFirebaseAuth, initFirebase } from "@/lib/firebase"
import { useConfig } from "@/lib/config-provider"
import { safeContinuePath, safeReturnTo } from "@/lib/auth/returnTo"
import { AuthCard, AuthFeedback, AuthFooter, AuthInput, AuthPrimaryButton } from "@/components/auth"
import { errorCode } from "@/lib/errors/errorCode"

type ActionMode = "verifyEmail" | "resetPassword" | "recoverEmail" | "revertSecondFactorAddition"
type Status = "loading" | "success" | "error" | "reset-form"

function AuthActionContent() {
  const config = useConfig()
  const searchParams = useSearchParams()
  const mode = searchParams.get("mode") as ActionMode | null
  const oobCode = searchParams.get("oobCode")
  const continueUrl = searchParams.get("continueUrl")

  const [status, setStatus] = useState<Status>("loading")
  const [message, setMessage] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [resetEmail, setResetEmail] = useState("")
  const [submitting, setSubmitting] = useState(false)
  // Resolved on the client, where the page's own origin is known. The server
  // render carries the fallback so the two never disagree.
  const [continueHref, setContinueHref] = useState("/login")
  const [continueLabel, setContinueLabel] = useState("Continue to Sign In")
  // An action code is single-use: running the handler twice (a re-render with
  // a new config object, or React's dev double-invoke) would spend the code on
  // the first pass and report "expired" on the second.
  const handledCode = useRef<string | null>(null)

  useEffect(() => {
    setContinueHref(safeContinuePath(continueUrl, window.location.origin))
  }, [continueUrl])

  useEffect(() => {
    if (!oobCode || !mode) {
      setStatus("error")
      setMessage("Invalid action link. It may have expired or already been used.")
      return
    }

    if (handledCode.current === oobCode) return
    handledCode.current = oobCode

    // Take the Firebase config from the runtime config, the same source the
    // auth provider uses. NEXT_PUBLIC_* values are inlined at build time, and a
    // deployment that sets them only on the running container leaves them
    // empty here — initializing with an empty key throws auth/invalid-api-key,
    // and because this effect runs before the auth provider's, that broken
    // instance is the one the whole app then shares (a root-level crash).
    // ConfigProvider only renders this page once the config has loaded.
    initFirebase({
      apiKey: config.firebaseApiKey,
      authDomain: config.firebaseAuthDomain,
      projectId: config.firebaseProjectId,
      appId: config.firebaseAppId,
    })

    handleAction(mode, oobCode)
  }, [mode, oobCode, config])

  async function handleAction(actionMode: ActionMode, code: string) {
    const auth = getFirebaseAuth()

    switch (actionMode) {
      case "verifyEmail":
        try {
          await applyActionCode(auth, code)
          // Someone verifying from the browser they signed up in is already
          // signed in. Refresh their user so the app sees the verified flag,
          // then send them straight back in rather than to the sign-in page.
          await auth.authStateReady()
          if (auth.currentUser) {
            try {
              await auth.currentUser.reload()
            } catch (err) {
              console.warn("verifyEmail user reload failed:", errorCode(err))
            }
            // Honor where the email asked to go (e.g. back to MFA enrollment);
            // a /login target means "the app" for someone already signed in.
            window.location.replace(
              safeReturnTo(safeContinuePath(continueUrl, window.location.origin))
            )
            return
          }
          setStatus("success")
          setMessage("Your email is verified.")
          setContinueLabel("Continue to Pablo")
        } catch (err) {
          console.error("verifyEmail action failed:", errorCode(err))
          setStatus("error")
          setMessage(
            `This verification link has expired or already been used (${errorCode(err)}).`
          )
        }
        break

      case "resetPassword":
        try {
          const email = await verifyPasswordResetCode(auth, code)
          setResetEmail(email)
          setStatus("reset-form")
        } catch (err) {
          console.error("resetPassword action failed:", errorCode(err))
          setStatus("error")
          setMessage(
            `This password reset link has expired or already been used (${errorCode(err)}).`
          )
        }
        break

      case "recoverEmail":
        try {
          const info = await checkActionCode(auth, code)
          await applyActionCode(auth, code)
          setStatus("success")
          setMessage(
            `Your email has been reverted to ${info.data.email}. If you didn't request this change, consider resetting your password.`
          )
        } catch (err) {
          console.error("recoverEmail action failed:", errorCode(err))
          setStatus("error")
          setMessage(
            `This email recovery link has expired or already been used (${errorCode(err)}).`
          )
        }
        break

      case "revertSecondFactorAddition":
        try {
          await applyActionCode(auth, code)
          setStatus("success")
          setMessage(
            "Two-factor authentication has been removed from your account. If you didn't request this, secure your account immediately."
          )
        } catch (err) {
          console.error("revertSecondFactorAddition action failed:", errorCode(err))
          setStatus("error")
          setMessage(`This link has expired or already been used (${errorCode(err)}).`)
        }
        break

      default:
        setStatus("error")
        setMessage("Unknown action.")
    }
  }

  async function handlePasswordReset(e: React.FormEvent) {
    e.preventDefault()
    if (newPassword !== confirmPassword) {
      setMessage("Passwords don't match.")
      return
    }
    if (newPassword.length < 15) {
      setMessage("Password must be at least 15 characters.")
      return
    }

    setSubmitting(true)
    setMessage("")

    try {
      const auth = getFirebaseAuth()
      await confirmPasswordReset(auth, oobCode!, newPassword)
      setStatus("success")
      setMessage("Your password has been reset.")
    } catch (err) {
      console.error("confirmPasswordReset failed:", errorCode(err))
      setStatus("error")
      setMessage(`Failed to reset password. The link may have expired (${errorCode(err)}).`)
    } finally {
      setSubmitting(false)
    }
  }

  const title: Record<ActionMode, string> = {
    verifyEmail: "Email Verification",
    resetPassword: "Reset Password",
    recoverEmail: "Email Recovery",
    revertSecondFactorAddition: "Two-Factor Authentication",
  }

  return (
    <AuthCard className="w-full max-w-md space-y-6 bg-card p-10 rounded-2xl shadow-xl border border-neutral-100">
      <div className="text-center">
        <h1 className="text-4xl font-display font-bold text-primary-600">Pablo</h1>
        <h2 className="mt-3 text-xl font-semibold text-neutral-800">
          {mode ? title[mode] || "Account Action" : "Account Action"}
        </h2>
      </div>

      {status === "loading" && (
        <div className="flex justify-center py-8">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary-600" />
        </div>
      )}

      {status === "success" && (
        <div className="space-y-4">
          <AuthFeedback variant="success" padding="4">
            {message}
          </AuthFeedback>
          <a
            href={continueHref}
            rel="noopener noreferrer"
            className="auth-primary block text-center"
          >
            {continueLabel}
          </a>
        </div>
      )}

      {status === "error" && (
        <div className="space-y-4">
          <AuthFeedback variant="error" padding="4">
            {message}
          </AuthFeedback>
          <a
            href="/login"
            className="auth-primary block text-center"
          >
            Back to Sign In
          </a>
        </div>
      )}

      {status === "reset-form" && (
        <form onSubmit={handlePasswordReset} className="space-y-4">
          <p className="text-sm text-neutral-600 text-center">
            Enter a new password for <strong>{resetEmail}</strong>
          </p>

          <AuthInput
            id="new-password"
            label="New Password"
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            placeholder="Min 15 characters"
            required
            minLength={15}
            autoFocus
          />

          <AuthInput
            id="confirm-password"
            label="Confirm Password"
            type="password"
            autoComplete="new-password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            placeholder="Confirm password"
            required
            minLength={15}
          />

          {message && <AuthFeedback variant="error">{message}</AuthFeedback>}

          <AuthPrimaryButton type="submit" disabled={submitting}>
            {submitting ? "Resetting..." : "Reset Password"}
          </AuthPrimaryButton>
        </form>
      )}

      <AuthFooter spacing="compact" />
    </AuthCard>
  )
}

export function FirebaseAuthActionScreen() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-primary-50 via-neutral-50 to-secondary-50">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary-600" />
        </div>
      }
    >
      <AuthActionContent />
    </Suspense>
  )
}
