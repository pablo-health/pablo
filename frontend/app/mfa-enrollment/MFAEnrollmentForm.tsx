// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState, useEffect } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import {
  TotpMultiFactorGenerator,
  TotpSecret,
  multiFactor,
  reauthenticateWithPopup,
  reauthenticateWithCredential,
  EmailAuthProvider,
  GoogleAuthProvider,
  sendEmailVerification,
  type User as FirebaseUser,
} from "firebase/auth"
import { QRCodeSVG } from "qrcode.react"
import { getFirebaseAuth } from "@/lib/firebase"
import { useAuth } from "@/lib/auth-context"
import { post } from "@/lib/api/client"
import { AuthFeedback, AuthInput, AuthPrimaryButton } from "@/components/auth"

export function MFAEnrollmentForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const returnTo = searchParams.get("returnTo")
  const { user, loading: authLoading } = useAuth()
  const [totpSecret, setTotpSecret] = useState<TotpSecret | null>(null)
  const [verificationCode, setVerificationCode] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const [showManualEntry, setShowManualEntry] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [needsReauth, setNeedsReauth] = useState(false)
  const [needsEmailVerification, setNeedsEmailVerification] = useState(false)
  const [verificationEmailSent, setVerificationEmailSent] = useState(false)
  const [reauthPassword, setReauthPassword] = useState("")

  // Record MFA enrollment in the backend and redirect to dashboard
  const recordEnrollmentAndRedirect = async (currentUser: FirebaseUser) => {
    const token = await currentUser.getIdToken()
    await post("/api/users/me/mfa-enrolled", {}, token)
    const destination = returnTo && returnTo.startsWith("/") && !returnTo.startsWith("//") ? returnTo : "/dashboard"
    router.push(destination)
  }

  // Try generating TOTP secret on mount (works if session is fresh enough)
  useEffect(() => {
    const generateSecret = async () => {
      const auth = getFirebaseAuth()
      const currentUser = auth.currentUser
      if (!currentUser || generating) return

      setGenerating(true)
      try {
        // Check if user already has a TOTP factor enrolled
        const enrolledFactors = multiFactor(currentUser).enrolledFactors
        const hasTotp = enrolledFactors.some((f) => f.factorId === "totp")
        if (hasTotp) {
          // Already enrolled in Firebase — just sync the backend and move on
          await recordEnrollmentAndRedirect(currentUser)
          return
        }

        await currentUser.getIdToken(true)
        const multiFactorSession = await multiFactor(currentUser).getSession()
        const secret = await TotpMultiFactorGenerator.generateSecret(multiFactorSession)
        setTotpSecret(secret)
      } catch (err) {
        const errCode = (err as { code?: string }).code
        console.error("MFA secret generation error:", errCode)
        if (errCode === "auth/maximum-second-factor-count-exceeded") {
          // Already has max factors — sync backend and redirect
          const currentUser = auth.currentUser
          if (currentUser) {
            await recordEnrollmentAndRedirect(currentUser)
            return
          }
        } else if (
          errCode === "auth/invalid-user-token" ||
          errCode === "auth/requires-recent-login"
        ) {
          // Session not fresh enough — need reauthentication via user gesture
          setNeedsReauth(true)
        } else if (errCode === "auth/unverified-email") {
          setNeedsEmailVerification(true)
        } else {
          setError(`Failed to generate MFA secret: ${errCode || "unknown error"}. Please refresh the page.`)
        }
      } finally {
        setGenerating(false)
      }
    }

    generateSecret()
  }, [user]) // eslint-disable-line react-hooks/exhaustive-deps

  const isPasswordUser = user?.providerData?.some(
    (p) => p.providerId === "password"
  )

  // Reauthenticate and retry — must be called from a user gesture (click)
  const handleReauthenticate = async (e?: React.FormEvent) => {
    e?.preventDefault()
    const auth = getFirebaseAuth()
    const currentUser = auth.currentUser
    if (!currentUser) return

    setError("")
    setGenerating(true)
    setNeedsReauth(false)

    try {
      if (isPasswordUser && reauthPassword) {
        const credential = EmailAuthProvider.credential(
          currentUser.email!,
          reauthPassword
        )
        await reauthenticateWithCredential(currentUser, credential)
      } else {
        const provider = new GoogleAuthProvider()
        await reauthenticateWithPopup(currentUser, provider)
      }

      // Now generate the TOTP secret with the fresh session
      const multiFactorSession = await multiFactor(currentUser).getSession()
      const secret = await TotpMultiFactorGenerator.generateSecret(multiFactorSession)
      setTotpSecret(secret)
    } catch (err) {
      const errCode = (err as { code?: string }).code
      console.error("Reauthentication/MFA error:", errCode)
      if (
        errCode === "auth/popup-closed-by-user" ||
        errCode === "auth/cancelled-popup-request"
      ) {
        setNeedsReauth(true)
        setError("Sign-in popup was closed. Please try again.")
      } else if (errCode === "auth/popup-blocked") {
        setNeedsReauth(true)
        setError("Popup was blocked by your browser. Please allow popups for this site.")
      } else if (
        errCode === "auth/wrong-password" ||
        errCode === "auth/invalid-credential"
      ) {
        setNeedsReauth(true)
        setError("Incorrect password. Please try again.")
      } else {
        setError(`Authentication failed: ${errCode || "unknown error"}. Please try again.`)
        setNeedsReauth(true)
      }
    } finally {
      setGenerating(false)
    }
  }

  const handleEnroll = async (e: React.FormEvent) => {
    e.preventDefault()
    const currentUser = getFirebaseAuth().currentUser
    if (!currentUser || !totpSecret) return

    setError("")
    setLoading(true)

    try {
      // Generate assertion from the secret and verification code
      const multiFactorAssertion = TotpMultiFactorGenerator.assertionForEnrollment(
        totpSecret,
        verificationCode
      )

      // Enroll the user with the assertion
      await multiFactor(currentUser).enroll(
        multiFactorAssertion,
        "Authenticator app"
      )

      // Record enrollment in backend and redirect
      await recordEnrollmentAndRedirect(currentUser)
    } catch (err) {
      const code = (err as { code?: string }).code
      if (code === "auth/invalid-verification-code") {
        setError("Invalid verification code. Please try again.")
      } else if (code === "auth/code-expired") {
        setError("Verification code expired. Please generate a new code in your app.")
      } else {
        setError("Enrollment failed. Please try again.")
        console.error("MFA enrollment error:", (err as { code?: string }).code)
      }
    } finally {
      setLoading(false)
    }
  }

  const handleCodeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    // Only allow digits and limit to 6 characters
    const value = e.target.value.replace(/\D/g, "").slice(0, 6)
    setVerificationCode(value)
  }

  if (!totpSecret) {
    return (
      <div className="card">
        {needsEmailVerification ? (
          <div className="py-8 space-y-4">
            <div className="text-center">
              <svg
                className="w-12 h-12 text-amber-500 mx-auto mb-3"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
                />
              </svg>
              <h3 className="text-lg font-semibold text-neutral-900 mb-2">
                Verify Your Email First
              </h3>
              <p className="text-sm text-neutral-600">
                You need to verify your email address before enabling MFA.
                Check your inbox (and spam folder) for the verification link.
              </p>
            </div>

            {error && <AuthFeedback variant="error">{error}</AuthFeedback>}

            {verificationEmailSent && (
              <AuthFeedback variant="success">
                Verification email sent. Check your inbox and spam folder.
              </AuthFeedback>
            )}

            <AuthPrimaryButton
              onClick={async () => {
                const currentUser = getFirebaseAuth().currentUser
                if (!currentUser) return
                try {
                  await sendEmailVerification(currentUser, {
                    url: `${window.location.origin}/mfa-enrollment`,
                  })
                  setVerificationEmailSent(true)
                  setError("")
                } catch {
                  setError("Failed to send verification email. Please wait a minute and try again.")
                }
              }}
              disabled={verificationEmailSent}
              className="w-full bg-primary-600 text-white px-4 py-3 rounded-lg font-medium hover:bg-primary-700 active:scale-[0.98] transition-all duration-200 disabled:opacity-50"
            >
              {verificationEmailSent ? "Email Sent" : "Resend Verification Email"}
            </AuthPrimaryButton>

            <button
              onClick={() => {
                setNeedsEmailVerification(false)
                setError("")
                window.location.reload()
              }}
              className="w-full text-sm text-primary-600 hover:text-primary-700 hover:underline"
            >
              I&apos;ve verified my email — retry
            </button>
          </div>
        ) : needsReauth ? (
          <div className="py-8 space-y-4">
            <p className="text-sm text-neutral-700 text-center">
              For security, please verify your identity before setting up MFA.
            </p>
            {error && (
              <AuthFeedback variant="error" padding="4">
                {error}
              </AuthFeedback>
            )}
            {isPasswordUser ? (
              <form onSubmit={handleReauthenticate} className="space-y-4">
                <AuthInput
                  id="reauth-password"
                  label="Enter your password to continue"
                  type="password"
                  autoComplete="current-password"
                  value={reauthPassword}
                  onChange={(e) => setReauthPassword(e.target.value)}
                  placeholder="Your password"
                  required
                  autoFocus
                />
                <AuthPrimaryButton
                  type="submit"
                  disabled={generating || !reauthPassword}
                  className="w-full bg-primary-600 text-white px-4 py-3 rounded-lg font-medium hover:bg-primary-700 active:scale-[0.98] transition-all duration-200 disabled:opacity-50"
                >
                  {generating ? "Verifying..." : "Verify Identity"}
                </AuthPrimaryButton>
              </form>
            ) : (
              <AuthPrimaryButton
                onClick={() => handleReauthenticate()}
                disabled={generating}
                className="w-full bg-primary-600 text-white px-4 py-3 rounded-lg font-medium hover:bg-primary-700 active:scale-[0.98] transition-all duration-200 disabled:opacity-50"
              >
                {generating ? "Verifying..." : "Verify Identity with Google"}
              </AuthPrimaryButton>
            )}
          </div>
        ) : error ? (
          <div className="py-8">
            <div className="mb-4">
              <AuthFeedback variant="error" padding="4">
                {error}
              </AuthFeedback>
            </div>
            <button
              onClick={() => window.location.reload()}
              className="w-full bg-primary-600 text-white px-4 py-2 rounded-lg hover:bg-primary-700"
            >
              Retry
            </button>
          </div>
        ) : (
          <div className="flex items-center justify-center py-12">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
            <p className="ml-3 text-neutral-600">
              {authLoading ? "Loading authentication..." : "Generating secure key..."}
            </p>
          </div>
        )}
      </div>
    )
  }

  const qrCodeUrl = totpSecret.generateQrCodeUrl(
    user?.email || "user@pablo.health",
    "Pablo"
  )

  return (
    <div className="card">
      <div className="space-y-6">
        {/* Step 1: Scan QR Code */}
        <div>
          <div className="flex items-center gap-2 mb-4">
            <div className="w-8 h-8 bg-primary-600 rounded-full flex items-center justify-center text-white font-bold text-sm">
              1
            </div>
            <h2 className="text-lg font-semibold text-neutral-900">
              Scan QR Code
            </h2>
          </div>

          <p className="text-sm text-neutral-700 mb-4">
            Use an authenticator app like Google Authenticator, Authy, or 1Password to scan
            this QR code:
          </p>

          <div className="bg-white p-6 rounded-lg border-2 border-neutral-200 flex justify-center">
            <QRCodeSVG
              value={qrCodeUrl}
              size={200}
              level="M"
              includeMargin={true}
            />
          </div>

          <button
            type="button"
            onClick={() => setShowManualEntry(!showManualEntry)}
            className="mt-3 text-sm text-primary-600 hover:text-primary-700 hover:underline"
          >
            {showManualEntry ? "Hide" : "Show"} manual entry key
          </button>

          {showManualEntry && (
            <div className="mt-3 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
              <p className="text-xs text-neutral-600 mb-2 font-medium">
                Manual Entry Key:
              </p>
              <code className="text-sm font-mono text-neutral-900 break-all">
                {totpSecret.secretKey}
              </code>
              <p className="text-xs text-neutral-500 mt-2">
                Enter this key manually in your authenticator app if you cannot scan the QR code.
              </p>
            </div>
          )}
        </div>

        {/* Step 2: Enter Verification Code */}
        <div>
          <div className="flex items-center gap-2 mb-4">
            <div className="w-8 h-8 bg-primary-600 rounded-full flex items-center justify-center text-white font-bold text-sm">
              2
            </div>
            <h2 className="text-lg font-semibold text-neutral-900">
              Enter Verification Code
            </h2>
          </div>

          <p className="text-sm text-neutral-700 mb-4">
            Enter the 6-digit code from your authenticator app to verify the setup:
          </p>

          <form onSubmit={handleEnroll} className="space-y-4">
            <AuthInput
              id="verification-code"
              label="Verification Code"
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              value={verificationCode}
              onChange={handleCodeChange}
              placeholder="000000"
              className="w-full px-4 py-3 border border-neutral-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent text-center text-2xl font-mono tracking-widest"
              maxLength={6}
              required
              autoComplete="one-time-code"
            />

            {error && <AuthFeedback variant="error">{error}</AuthFeedback>}

            <AuthPrimaryButton
              type="submit"
              disabled={loading || verificationCode.length !== 6}
            >
              {loading ? "Verifying..." : "Enable MFA"}
            </AuthPrimaryButton>
          </form>
        </div>

        {/* Help Text */}
        <div className="bg-amber-50 border-l-4 border-amber-500 p-4 rounded-r-lg">
          <div className="flex items-start gap-3">
            <svg
              className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
              />
            </svg>
            <div>
              <h3 className="text-sm font-semibold text-amber-900 mb-1">
                Save Your Recovery Options
              </h3>
              <p className="text-sm text-amber-800 leading-relaxed">
                Make sure you have access to your authenticator app before enabling MFA.
                If you lose access to your device, you may need to contact support to regain
                access to your account.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
