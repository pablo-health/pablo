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
import {
  AuthCard,
  AuthDivider,
  AuthFeedback,
  AuthFooter,
  AuthGoogleButton,
  AuthHeader,
  AuthInput,
  AuthPrimaryButton,
} from "@/components/auth"

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
      <AuthCard>
        <AuthHeader
          title="Invalid Request"
          titleColor="red"
          subtitle={
            !redirectUri
              ? "Missing redirect_uri parameter. This page must be opened from the Pablo app."
              : "Invalid redirect_uri scheme. Only approved native apps may use this page."
          }
        />
      </AuthCard>
    )
  }

  // Redirecting screen — browser stays on this page after opening the app
  if (redirecting) {
    return (
      <AuthCard>
        <AuthHeader
          title="Sign-in complete"
          subtitle="You can close this tab and return to Pablo."
        />
      </AuthCard>
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
      <AuthCard>
        <AuthHeader
          title="Two-Factor Authentication"
          subtitle="Enter the code from your authenticator app"
        />
        <form onSubmit={handleMfaVerify} className="space-y-4">
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
      </AuthCard>
    )
  }

  // Email Verification Sent Screen
  if (verificationSent) {
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
        <AuthPrimaryButton
          onClick={() => {
            setVerificationSent(false)
            setIsSignUp(false)
          }}
        >
          Back to Sign In
        </AuthPrimaryButton>
      </AuthCard>
    )
  }

  return (
    <AuthCard>
      <AuthHeader
        title="Sign in to Pablo"
        titleSize="4xl"
        subtitle="Sign in to connect your desktop app"
      />

      <div className="mt-8 space-y-4">
        <form
          onSubmit={isSignUp ? handleEmailSignUp : handleEmailLogin}
          className="space-y-4"
        >
          <AuthInput
            id="email"
            label="Email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            required
          />

          <AuthInput
            id="password"
            label="Password"
            type="password"
            autoComplete={isSignUp ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={isSignUp ? "Choose a password (min 15 chars)" : "Password"}
            required
            minLength={isSignUp ? 15 : undefined}
          />

          {error && <AuthFeedback variant="error">{error}</AuthFeedback>}

          {resetSent && (
            <AuthFeedback variant="success">
              If that email exists, a password reset link has been sent.
            </AuthFeedback>
          )}

          <AuthPrimaryButton type="submit" disabled={loading}>
            {loading
              ? isSignUp
                ? "Creating account..."
                : "Signing in..."
              : isSignUp
                ? "Create Account"
                : "Sign In"}
          </AuthPrimaryButton>

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

        <AuthDivider />

        <AuthGoogleButton onClick={handleGoogleLogin} />

        <p className="mt-6 text-center text-sm text-neutral-500">
          By signing in, you agree to our Terms of Service and Privacy Policy
        </p>
      </div>

      <AuthFooter />
    </AuthCard>
  )
}
