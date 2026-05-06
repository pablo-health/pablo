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
  getMultiFactorResolver,
  type MultiFactorError,
  type MultiFactorResolver,
} from "firebase/auth"
import { getFirebaseAuth } from "@/lib/firebase"
import { useConfig } from "@/lib/config"
import { firebaseAuthErrorOutcome } from "@/lib/auth-errors"
import {
  AuthCard,
  AuthDivider,
  AuthFeedback,
  AuthFooter,
  AuthGoogleButton,
  AuthHeader,
  AuthInput,
  AuthLinkButton,
  AuthPrimaryButton,
  MfaChallengeScreen,
  VerifyEmailScreen,
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
  const [mfaResolver, setMfaResolver] = useState<MultiFactorResolver | null>(null)

  // Validate redirect_uri
  const redirectUri = searchParams.get("redirect_uri")
  // OAuth state (RFC 6749 §10.12) — must be echoed back to redirect_uri unmodified.
  const state = searchParams.get("state")
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
            const returnParams = new URLSearchParams({ redirect_uri: redirectUri! })
            if (state) returnParams.set("state", state)
            const returnUrl = `/native-auth?${returnParams.toString()}`
            window.location.href = `/mfa-enrollment?returnTo=${encodeURIComponent(returnUrl)}`
            return
          }
          throw new Error("Failed to generate authorization code")
        }

        const { code } = await res.json()
        const callbackUrl = new URL(redirectUri!)
        callbackUrl.searchParams.set("code", code)
        if (state) callbackUrl.searchParams.set("state", state)
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

  const handleEmailLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")
    setLoading(true)

    try {
      const credential = await signInWithEmailAndPassword(getFirebaseAuth(), email, password)
      await redirectToApp(credential.user)
    } catch (err) {
      const outcome = firebaseAuthErrorOutcome(err, "sign-in")
      if (outcome.kind === "mfa-required") {
        handleMfaRequired(err as MultiFactorError)
      } else if (outcome.kind === "message") {
        setError(outcome.message)
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
      const outcome = firebaseAuthErrorOutcome(err, "sign-up")
      if (outcome.kind === "message") setError(outcome.message)
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
      const outcome = firebaseAuthErrorOutcome(err, "google")
      if (outcome.kind === "mfa-required") {
        handleMfaRequired(err as MultiFactorError)
      } else if (outcome.kind === "popup-blocked") {
        setError("Popup was blocked by your browser. Please allow popups for this site.")
      } else if (outcome.kind === "message") {
        setError(outcome.message)
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

  if (mfaResolver) {
    return (
      <MfaChallengeScreen
        resolver={mfaResolver}
        onSuccess={(credential) => redirectToApp(credential.user)}
        onCancel={() => {
          setMfaResolver(null)
          setError("")
        }}
      />
    )
  }

  if (verificationSent) {
    return (
      <VerifyEmailScreen
        email={email}
        onBack={() => {
          setVerificationSent(false)
          setIsSignUp(false)
        }}
      />
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
              <AuthLinkButton onClick={handleForgotPassword}>
                Forgot password?
              </AuthLinkButton>
            )}
            <AuthLinkButton
              onClick={() => {
                setIsSignUp(!isSignUp)
                setError("")
              }}
            >
              {isSignUp ? "Already have an account?" : "Create account"}
            </AuthLinkButton>
          </div>
        </form>

        <AuthDivider />

        <AuthGoogleButton onClick={handleGoogleLogin} />

        <p className="mt-6 text-center text-sm text-neutral-500">
          By signing in, you agree to our{" "}
          <a
            href="https://pablo.health/terms"
            target="_blank"
            rel="noopener noreferrer"
            className="underline hover:text-neutral-700"
          >
            Terms of Service
          </a>{" "}
          and{" "}
          <a
            href="https://pablo.health/privacy/product"
            target="_blank"
            rel="noopener noreferrer"
            className="underline hover:text-neutral-700"
          >
            Privacy Policy
          </a>
          .
        </p>
      </div>

      <AuthFooter />
    </AuthCard>
  )
}
