// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState, useCallback } from "react"
import { useSearchParams } from "next/navigation"
import {
  signInWithPopup,
  signInWithEmailAndPassword,
  sendPasswordResetEmail,
  createUserWithEmailAndPassword,
  sendEmailVerification,
  GoogleAuthProvider,
  TotpMultiFactorGenerator,
  getMultiFactorResolver,
  type MultiFactorError,
  type MultiFactorResolver,
} from "firebase/auth"
import { getFirebaseAuth } from "@/lib/firebase"
import { useConfig } from "@/lib/config"

const ALLOWED_SCHEMES = ["pablohealth", "therapyrecorder"]

export default function NativeAuthPage() {
  const searchParams = useSearchParams()
  const config = useConfig()
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const [resetSent, setResetSent] = useState(false)
  const [isSignUp, setIsSignUp] = useState(false)
  const [verificationSent, setVerificationSent] = useState(false)
  const [redirecting, setRedirecting] = useState(false)

  // MFA challenge state
  const [mfaResolver, setMfaResolver] = useState<MultiFactorResolver | null>(null)
  const [totpCode, setTotpCode] = useState("")

  // Validate redirect_uri
  const redirectUri = searchParams.get("redirect_uri")
  const isValidRedirectUri = (() => {
    if (!redirectUri) return false
    try {
      const url = new URL(redirectUri)
      const scheme = url.protocol.replace(":", "")
      // Allow custom URL schemes (macOS)
      if (ALLOWED_SCHEMES.includes(scheme)) return true
      // Allow loopback for native apps (RFC 8252 Section 7.3)
      if (scheme === "http" && (url.hostname === "localhost" || url.hostname === "127.0.0.1")) return true
      return false
    } catch {
      return false
    }
  })()

  const redirectToApp = useCallback(
    async (user: { getIdToken: () => Promise<string>; refreshToken: string }) => {
      setRedirecting(true)
      try {
        const idToken = await user.getIdToken()

        // Exchange tokens for a one-time authorization code (RFC 8252)
        // so raw tokens never appear in URLs
        const res = await fetch(`${config.apiUrl}/api/auth/native/code`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            id_token: idToken,
            refresh_token: user.refreshToken,
            redirect_uri: redirectUri,
          }),
        })

        if (!res.ok) {
          const data = await res.json().catch(() => null)
          const errorCode = data?.detail?.error?.code ?? data?.error?.code
          if (res.status === 403 && errorCode === "MFA_REQUIRED") {
            const returnUrl = `/native-auth?redirect_uri=${encodeURIComponent(redirectUri!)}`
            window.location.href = `/mfa-enrollment?returnTo=${encodeURIComponent(returnUrl)}`
            return
          }
          throw new Error("Failed to generate authorization code")
        }

        const { code } = await res.json()
        const callbackUrl = new URL(redirectUri!)
        callbackUrl.searchParams.set("code", code)
        window.location.href = callbackUrl.toString()
      } catch {
        setError("Failed to get authentication tokens.")
        setRedirecting(false)
      }
    },
    [redirectUri, config.apiUrl]
  )

  // If redirect_uri is invalid, show error immediately
  if (!isValidRedirectUri) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-primary-50 via-neutral-50 to-secondary-50">
        <div className="w-full max-w-md space-y-8 bg-white p-10 rounded-2xl shadow-xl border border-neutral-100">
          <div className="text-center">
            <h1 className="text-3xl font-display font-bold text-red-600">
              Invalid Request
            </h1>
            <p className="mt-3 text-neutral-600">
              {!redirectUri
                ? "Missing redirect_uri parameter. This page must be opened from the Pablo app."
                : "Invalid redirect_uri scheme. Only approved native apps may use this page."}
            </p>
          </div>
        </div>
      </div>
    )
  }

  // Redirecting screen — browser stays on this page after opening the app
  if (redirecting) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-primary-50 via-neutral-50 to-secondary-50">
        <div className="w-full max-w-md space-y-8 bg-white p-10 rounded-2xl shadow-xl border border-neutral-100">
          <div className="text-center">
            <h1 className="text-3xl font-display font-bold text-primary-600">
              Sign-in complete
            </h1>
            <p className="mt-3 text-neutral-600">
              You can close this tab and return to Pablo.
            </p>
          </div>
        </div>
      </div>
    )
  }

  const handleMfaRequired = (err: MultiFactorError) => {
    const resolver = getMultiFactorResolver(getFirebaseAuth(), err)
    setMfaResolver(resolver)
    setError("")
  }

  const handleMfaVerify = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!mfaResolver) return

    setError("")
    setLoading(true)

    try {
      const totpHint = mfaResolver.hints.find(
        (hint) => hint.factorId === TotpMultiFactorGenerator.FACTOR_ID
      )

      if (!totpHint) {
        setError("No TOTP factor found. Please contact support.")
        return
      }

      const assertion = TotpMultiFactorGenerator.assertionForSignIn(
        totpHint.uid,
        totpCode
      )

      const result = await mfaResolver.resolveSignIn(assertion)
      await redirectToApp(result.user)
    } catch (err) {
      const code = (err as { code?: string }).code
      if (code === "auth/invalid-verification-code") {
        setError("Invalid verification code. Please try again.")
      } else {
        setError("MFA verification failed. Please try again.")
      }
    } finally {
      setLoading(false)
    }
  }

  const handleEmailLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")
    setLoading(true)

    try {
      const credential = await signInWithEmailAndPassword(getFirebaseAuth(), email, password)
      await redirectToApp(credential.user)
    } catch (err) {
      const code = (err as { code?: string }).code
      if (code === "auth/multi-factor-auth-required") {
        handleMfaRequired(err as MultiFactorError)
      } else if (
        code === "auth/invalid-credential" ||
        code === "auth/user-not-found" ||
        code === "auth/wrong-password"
      ) {
        setError("Invalid email or password")
      } else if (code === "auth/too-many-requests") {
        setError("Too many attempts. Please try again later.")
      } else {
        setError("Login failed. Please try again.")
      }
    } finally {
      setLoading(false)
    }
  }

  const handleEmailSignUp = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")
    setLoading(true)

    try {
      const credential = await createUserWithEmailAndPassword(
        getFirebaseAuth(),
        email,
        password
      )
      await sendEmailVerification(credential.user, {
        url: `${window.location.origin}/login`,
      })
      setVerificationSent(true)
    } catch (err) {
      const code = (err as { code?: string }).code
      if (code === "auth/email-already-in-use") {
        setError("An account with this email already exists. Try signing in.")
      } else if (code === "auth/weak-password" || code === "auth/password-does-not-meet-requirements") {
        setError("Password must be at least 15 characters.")
      } else if (code === "auth/admin-restricted-operation") {
        setError("Sign-up is restricted. Please contact your administrator.")
      } else if (code === "auth/blocking-function-error-response") {
        const message = (err as { message?: string }).message
        setError(message || "Sign-up blocked by administrator.")
      } else {
        setError(`Sign-up failed (${code || "unknown"}). Please try again.`)
      }
    } finally {
      setLoading(false)
    }
  }

  const handleGoogleLogin = async () => {
    setError("")
    const auth = getFirebaseAuth()
    const provider = new GoogleAuthProvider()

    try {
      const credential = await signInWithPopup(auth, provider)
      await redirectToApp(credential.user)
    } catch (err) {
      const code = (err as { code?: string }).code
      const message = (err as { message?: string }).message
      if (code === "auth/multi-factor-auth-required") {
        handleMfaRequired(err as MultiFactorError)
      } else if (
        code === "auth/popup-closed-by-user" ||
        code === "auth/cancelled-popup-request"
      ) {
        // Benign popup errors, do nothing
      } else if (code === "auth/popup-blocked") {
        setError("Popup was blocked by your browser. Please allow popups for this site.")
      } else if (code === "auth/blocking-function-error-response") {
        setError(message || "Sign-in blocked by administrator.")
      } else {
        setError(`Google sign-in failed (${code || "unknown"}). Please try again.`)
      }
    }
  }

  const handleForgotPassword = async () => {
    if (!email) {
      setError("Enter your email address first, then click Forgot password")
      return
    }
    setError("")
    try {
      await sendPasswordResetEmail(getFirebaseAuth(), email)
      setResetSent(true)
    } catch {
      // Don't reveal whether email exists (security)
      setResetSent(true)
    }
  }

  // MFA Challenge Screen
  if (mfaResolver) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-primary-50 via-neutral-50 to-secondary-50">
        <div className="w-full max-w-md space-y-8 bg-white p-10 rounded-2xl shadow-xl border border-neutral-100">
          <div className="text-center">
            <h1 className="text-3xl font-display font-bold text-primary-600">
              Two-Factor Authentication
            </h1>
            <p className="mt-3 text-neutral-600">
              Enter the code from your authenticator app
            </p>
          </div>

          <form onSubmit={handleMfaVerify} className="space-y-4">
            <div>
              <label
                htmlFor="totp-code"
                className="block text-sm font-medium text-neutral-700 mb-1"
              >
                Verification Code
              </label>
              <input
                id="totp-code"
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
            </div>

            {error && (
              <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
                <p className="text-sm text-red-600">{error}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={loading || totpCode.length !== 6}
              className="w-full bg-primary-600 text-white px-6 py-3 rounded-lg font-medium hover:bg-primary-700 active:scale-[0.98] transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? "Verifying..." : "Verify"}
            </button>

            <button
              type="button"
              onClick={() => {
                setMfaResolver(null)
                setTotpCode("")
                setError("")
              }}
              className="w-full text-sm text-primary-600 hover:text-primary-700 hover:underline"
            >
              Back to sign in
            </button>
          </form>
        </div>
      </div>
    )
  }

  // Email Verification Sent Screen
  if (verificationSent) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-primary-50 via-neutral-50 to-secondary-50">
        <div className="w-full max-w-md space-y-8 bg-white p-10 rounded-2xl shadow-xl border border-neutral-100">
          <div className="text-center">
            <h1 className="text-3xl font-display font-bold text-primary-600">
              Check Your Email
            </h1>
            <p className="mt-3 text-neutral-600">
              We sent a verification link to <strong>{email}</strong>. Please
              verify your email before signing in.
            </p>
          </div>
          <button
            onClick={() => {
              setVerificationSent(false)
              setIsSignUp(false)
            }}
            className="w-full bg-primary-600 text-white px-6 py-3 rounded-lg font-medium hover:bg-primary-700 active:scale-[0.98] transition-all duration-200"
          >
            Back to Sign In
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-primary-50 via-neutral-50 to-secondary-50">
      <div className="w-full max-w-md space-y-8 bg-white p-10 rounded-2xl shadow-xl border border-neutral-100">
        <div className="text-center">
          <h1 className="text-4xl font-display font-bold text-primary-600">
            Sign in to Pablo
          </h1>
          <p className="mt-3 text-neutral-600">
            Sign in to connect your desktop app
          </p>
        </div>

        <div className="mt-8 space-y-4">
          <form
            onSubmit={isSignUp ? handleEmailSignUp : handleEmailLogin}
            className="space-y-4"
          >
            <div>
              <label
                htmlFor="email"
                className="block text-sm font-medium text-neutral-700 mb-1"
              >
                Email
              </label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full px-4 py-2 border border-neutral-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                required
              />
            </div>

            <div>
              <label
                htmlFor="password"
                className="block text-sm font-medium text-neutral-700 mb-1"
              >
                Password
              </label>
              <input
                id="password"
                type="password"
                autoComplete={isSignUp ? "new-password" : "current-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={isSignUp ? "Choose a password (min 15 chars)" : "Password"}
                className="w-full px-4 py-2 border border-neutral-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                required
                minLength={isSignUp ? 15 : undefined}
              />
            </div>

            {error && (
              <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
                <p className="text-sm text-red-600">{error}</p>
              </div>
            )}

            {resetSent && (
              <div className="p-3 bg-green-50 border border-green-200 rounded-lg">
                <p className="text-sm text-green-700">
                  If that email exists, a password reset link has been sent.
                </p>
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-primary-600 text-white px-6 py-3 rounded-lg font-medium hover:bg-primary-700 active:scale-[0.98] transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading
                ? isSignUp
                  ? "Creating account..."
                  : "Signing in..."
                : isSignUp
                  ? "Create Account"
                  : "Sign In"}
            </button>

            <div className="flex items-center justify-between text-sm">
              {!isSignUp && (
                <button
                  type="button"
                  onClick={handleForgotPassword}
                  className="text-primary-600 hover:text-primary-700 hover:underline"
                >
                  Forgot password?
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  setIsSignUp(!isSignUp)
                  setError("")
                }}
                className="text-primary-600 hover:text-primary-700 hover:underline"
              >
                {isSignUp ? "Already have an account?" : "Create account"}
              </button>
            </div>
          </form>

          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-neutral-300"></div>
            </div>
            <div className="relative flex justify-center text-sm">
              <span className="px-2 bg-white text-neutral-500">or</span>
            </div>
          </div>

          <button
            onClick={handleGoogleLogin}
            type="button"
            className="w-full flex items-center justify-center gap-3 bg-white border-2 border-neutral-300 text-neutral-700 px-6 py-3.5 rounded-lg font-medium hover:bg-neutral-50 hover:border-primary-400 hover:shadow-md active:scale-[0.98] transition-all duration-200"
          >
            <svg className="w-5 h-5" viewBox="0 0 24 24">
              <path
                fill="currentColor"
                d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                style={{ fill: "#4285F4" }}
              />
              <path
                fill="currentColor"
                d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                style={{ fill: "#34A853" }}
              />
              <path
                fill="currentColor"
                d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                style={{ fill: "#FBBC05" }}
              />
              <path
                fill="currentColor"
                d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                style={{ fill: "#EA4335" }}
              />
            </svg>
            Sign in with Google
          </button>

          <p className="mt-6 text-center text-sm text-neutral-500">
            By signing in, you agree to our Terms of Service and Privacy Policy
          </p>
        </div>

        <div className="mt-6 pt-6 border-t border-neutral-200">
          <p className="text-xs text-neutral-500 text-center leading-relaxed">
            This platform is HIPAA compliant and uses industry-standard
            encryption to protect your data
          </p>
        </div>
      </div>
    </div>
  )
}
