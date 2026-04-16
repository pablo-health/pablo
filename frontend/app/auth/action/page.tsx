// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useState } from "react"
import { useSearchParams } from "next/navigation"
import { Suspense } from "react"
import {
  applyActionCode,
  checkActionCode,
  confirmPasswordReset,
  verifyPasswordResetCode,
} from "firebase/auth"
import { getFirebaseAuth, initFirebase } from "@/lib/firebase"

type ActionMode = "verifyEmail" | "resetPassword" | "recoverEmail" | "revertSecondFactorAddition"
type Status = "loading" | "success" | "error" | "reset-form"

function AuthActionContent() {
  const searchParams = useSearchParams()
  const mode = searchParams.get("mode") as ActionMode | null
  const oobCode = searchParams.get("oobCode")
  const continueUrl = searchParams.get("continueUrl")
  const apiKey = searchParams.get("apiKey")

  const [status, setStatus] = useState<Status>("loading")
  const [message, setMessage] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [resetEmail, setResetEmail] = useState("")
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!oobCode || !mode) {
      setStatus("error")
      setMessage("Invalid action link. It may have expired or already been used.")
      return
    }

    // Initialize Firebase if not already done — the page is accessed
    // directly from email links, so the config provider may not be loaded.
    const projectId = process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID || ""
    const authDomain = process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN || ""
    const appId = process.env.NEXT_PUBLIC_FIREBASE_APP_ID || ""
    initFirebase({
      apiKey: apiKey || process.env.NEXT_PUBLIC_FIREBASE_API_KEY || "",
      authDomain,
      projectId,
      appId,
    })

    handleAction(mode, oobCode)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, oobCode])

  async function handleAction(actionMode: ActionMode, code: string) {
    const auth = getFirebaseAuth()

    switch (actionMode) {
      case "verifyEmail":
        try {
          await applyActionCode(auth, code)
          setStatus("success")
          setMessage("Your email has been verified.")
        } catch {
          setStatus("error")
          setMessage("This verification link has expired or already been used.")
        }
        break

      case "resetPassword":
        try {
          const email = await verifyPasswordResetCode(auth, code)
          setResetEmail(email)
          setStatus("reset-form")
        } catch {
          setStatus("error")
          setMessage("This password reset link has expired or already been used.")
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
        } catch {
          setStatus("error")
          setMessage("This email recovery link has expired or already been used.")
        }
        break

      case "revertSecondFactorAddition":
        try {
          await applyActionCode(auth, code)
          setStatus("success")
          setMessage(
            "Two-factor authentication has been removed from your account. If you didn't request this, secure your account immediately."
          )
        } catch {
          setStatus("error")
          setMessage("This link has expired or already been used.")
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
    } catch {
      setStatus("error")
      setMessage("Failed to reset password. The link may have expired.")
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
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-primary-50 via-neutral-50 to-secondary-50">
      <div className="w-full max-w-md space-y-6 bg-white p-10 rounded-2xl shadow-xl border border-neutral-100">
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
            <div className="p-4 bg-green-50 border border-green-200 rounded-lg">
              <p className="text-sm text-green-800">{message}</p>
            </div>
            <a
              href={continueUrl || "/login"}
              className="block w-full text-center bg-primary-600 text-white px-6 py-3 rounded-lg font-medium hover:bg-primary-700 active:scale-[0.98] transition-all duration-200"
            >
              Continue to Sign In
            </a>
          </div>
        )}

        {status === "error" && (
          <div className="space-y-4">
            <div className="p-4 bg-red-50 border border-red-200 rounded-lg">
              <p className="text-sm text-red-600">{message}</p>
            </div>
            <a
              href="/login"
              className="block w-full text-center bg-primary-600 text-white px-6 py-3 rounded-lg font-medium hover:bg-primary-700 active:scale-[0.98] transition-all duration-200"
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

            <div>
              <label htmlFor="new-password" className="block text-sm font-medium text-neutral-700 mb-1">
                New Password
              </label>
              <input
                id="new-password"
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="Min 15 characters"
                className="w-full px-4 py-2 border border-neutral-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                required
                minLength={15}
                autoFocus
              />
            </div>

            <div>
              <label htmlFor="confirm-password" className="block text-sm font-medium text-neutral-700 mb-1">
                Confirm Password
              </label>
              <input
                id="confirm-password"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Confirm password"
                className="w-full px-4 py-2 border border-neutral-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                required
                minLength={15}
              />
            </div>

            {message && (
              <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
                <p className="text-sm text-red-600">{message}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="w-full bg-primary-600 text-white px-6 py-3 rounded-lg font-medium hover:bg-primary-700 active:scale-[0.98] transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? "Resetting..." : "Reset Password"}
            </button>
          </form>
        )}

        <div className="pt-4 border-t border-neutral-200">
          <p className="text-xs text-neutral-500 text-center leading-relaxed">
            This platform is HIPAA compliant and uses industry-standard encryption to protect your
            data
          </p>
        </div>
      </div>
    </div>
  )
}

export default function AuthActionPage() {
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
