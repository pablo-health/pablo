// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Firebase implementation of the `/native-auth` surface — the companion
 * desktop app's sign-in handoff. Authenticates with Firebase, then
 * exchanges the id token for a one-time authorization code (RFC 8252)
 * handed back to the native app via its custom-scheme redirect.
 */

import { useState, useCallback, useEffect } from "react"
import { useSearchParams } from "next/navigation"
import {
  signInWithPopup,
  signInWithEmailAndPassword,
  signInWithCustomToken,
  sendPasswordResetEmail,
  createUserWithEmailAndPassword,
  sendEmailVerification,
  GoogleAuthProvider,
  getMultiFactorResolver,
  type MultiFactorError,
  type MultiFactorResolver,
} from "firebase/auth"
import {
  startAuthentication,
  browserSupportsWebAuthn,
  WebAuthnError,
} from "@simplewebauthn/browser"
import { Fingerprint } from "lucide-react"
import { getFirebaseAuth } from "@/lib/firebase"
import { useConfig } from "@/lib/config"
import { beginAuthentication, finishAuthentication } from "@/lib/api/passkey"
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
  AuthOutlineButton,
  AuthPrimaryButton,
  MfaChallengeScreen,
  RecoveryCodeScreen,
  VerifyEmailScreen,
} from "@/components/auth"

const ALLOWED_SCHEMES = ["pablohealth", "therapyrecorder"]

export function FirebaseNativeAuthScreen() {
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
  const [showRecoveryCode, setShowRecoveryCode] = useState(false)

  // Only offer passkey sign-in where the browser can actually run the
  // ceremony — resolved client-side after mount to avoid an SSR mismatch.
  // Mirrors the `/login` surface; without it a clinician whose second factor
  // is a passkey has no way to satisfy it here and gets pushed into
  // authenticator-app enrollment they don't need.
  const [passkeySupported, setPasskeySupported] = useState(false)
  useEffect(() => {
    setPasskeySupported(config.passkeysEnabled && browserSupportsWebAuthn())
  }, [config.passkeysEnabled])

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
          const data = await res.json().catch((err) => {
            console.error("authorize response was not JSON:", err)
            return null
          })
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
    [redirectUri, state, config.apiUrl]
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

  const handlePasskeyLogin = async () => {
    setError("")
    setLoading(true)
    try {
      const options = await beginAuthentication()
      const assertion = await startAuthentication({ optionsJSON: options })
      const { custom_token } = await finishAuthentication(assertion)
      // The custom token carries the verified passkey factor (pablo_amr), so
      // the session it mints is already second-factor satisfied — the native
      // code exchange accepts it without a further challenge.
      const credential = await signInWithCustomToken(getFirebaseAuth(), custom_token)
      await redirectToApp(credential.user)
    } catch (err) {
      // User dismissed the platform prompt — leave the form untouched.
      if (err instanceof WebAuthnError && err.name === "NotAllowedError") return
      setError("Passkey sign-in failed. Try again, or use your email and password.")
    } finally {
      setLoading(false)
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

  if (showRecoveryCode) {
    return (
      <RecoveryCodeScreen
        initialEmail={email}
        onSuccess={(credential) => redirectToApp(credential.user)}
        onCancel={() => {
          setError("")
          setShowRecoveryCode(false)
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

        {!isSignUp && passkeySupported && (
          <div className="space-y-2">
            <AuthOutlineButton
              type="button"
              onClick={handlePasskeyLogin}
              disabled={loading}
              className="w-full flex items-center justify-center gap-3 bg-white border-2 border-neutral-300 text-neutral-700 px-6 py-3.5 rounded-lg font-medium hover:bg-neutral-50 hover:border-primary-400 hover:shadow-md active:scale-[0.98] transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Fingerprint className="h-5 w-5" />
              Sign in with a passkey
            </AuthOutlineButton>
            <div className="text-center">
              <AuthLinkButton
                size="sm"
                onClick={() => {
                  setError("")
                  setShowRecoveryCode(true)
                }}
              >
                Lost your passkey? Use a recovery code
              </AuthLinkButton>
            </div>
          </div>
        )}

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
