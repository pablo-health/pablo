// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import {
  signInWithPopup,
  signInWithRedirect,
  signInWithEmailAndPassword,
  sendPasswordResetEmail,
  createUserWithEmailAndPassword,
  sendEmailVerification,
  GoogleAuthProvider,
  getMultiFactorResolver,
  type MultiFactorError,
  type MultiFactorResolver,
  type UserCredential,
} from "firebase/auth"
import { getFirebaseAuth } from "@/lib/firebase"
import { useAuth } from "@/lib/auth-context"
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

type LoginStep = "sign-in" | "mfa" | "verify-email"

function getUrlParam(name: string): string {
  if (typeof window === "undefined") return ""
  const params = new URLSearchParams(window.location.search)
  return params.get(name) || ""
}

export default function LoginPage() {
  const router = useRouter()
  const { user, loading: authLoading } = useAuth()

  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const [resetSent, setResetSent] = useState(false)
  const [resendSent, setResendSent] = useState(false)
  const [isSignUp, setIsSignUp] = useState(false)
  const [verifyEmailError, setVerifyEmailError] = useState("")

  const [step, setStep] = useState<LoginStep>("sign-in")
  const [mfaResolver, setMfaResolver] = useState<MultiFactorResolver | null>(null)

  // Show notice when redirected from idle timeout
  useEffect(() => {
    const reason = getUrlParam("reason")
    if (reason === "idle_timeout") {
      setError("You were signed out due to inactivity.")
      window.history.replaceState({}, "", "/login")
    }
  }, [])

  // Exchange setup token from marketing signup to pre-fill email
  useEffect(() => {
    const setupToken = getUrlParam("setup")
    if (!setupToken) return

    // Clean the URL immediately so the token isn't in browser history
    window.history.replaceState({}, "", "/login")

    fetch("/api/auth/exchange-setup-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: setupToken }),
    })
      .then(res => res.json())
      .then(data => {
        if (data.email) {
          setEmail(data.email)
          setIsSignUp(true)
          // Override browser autofill by setting DOM value directly after a tick
          setTimeout(() => {
            const emailEl = document.getElementById("email") as HTMLInputElement | null
            if (emailEl) emailEl.value = data.email
            document.getElementById("password")?.focus()
          }, 200)
        }
      })
      .catch(() => {})  // Token expired or invalid — user types email manually
  }, [])

  // Redirect to dashboard when already authenticated (but not during signup flow)
  useEffect(() => {
    if (user && !authLoading && step !== "verify-email" && !isSignUp) {
      router.push("/dashboard")
    }
  }, [user, authLoading, router, step, isSignUp])

  const handleMfaRequired = (err: MultiFactorError) => {
    const resolver = getMultiFactorResolver(getFirebaseAuth(), err)
    setMfaResolver(resolver)
    setStep("mfa")
    setError("")
  }

  const finishLogin = async (credential: UserCredential) => {
    const idToken = await credential.user.getIdToken()
    await fetch("/api/login", {
      method: "POST",
      headers: { Authorization: `Bearer ${idToken}` },
    })
    router.push("/dashboard")
  }

  const handleEmailLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")
    setLoading(true)

    try {
      const credential = await signInWithEmailAndPassword(getFirebaseAuth(), email, password)
      await finishLogin(credential)
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

    if (password !== confirmPassword) {
      setError("Passwords do not match.")
      return
    }

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
      setStep("verify-email")
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
      const result = await signInWithPopup(auth, provider)
      await finishLogin(result)
    } catch (err) {
      const outcome = firebaseAuthErrorOutcome(err, "google")
      if (outcome.kind === "mfa-required") {
        handleMfaRequired(err as MultiFactorError)
      } else if (outcome.kind === "popup-blocked") {
        console.log("Popup blocked, falling back to redirect")
        await signInWithRedirect(auth, provider)
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

  if (step === "mfa" && mfaResolver) {
    return (
      <MfaChallengeScreen
        resolver={mfaResolver}
        onSuccess={finishLogin}
        onCancel={() => {
          setMfaResolver(null)
          setError("")
          setStep("sign-in")
        }}
      />
    )
  }

  if (step === "verify-email") {
    const handleResendVerification = async () => {
      const auth = getFirebaseAuth()
      if (!auth.currentUser) {
        setVerifyEmailError("Session expired. Please sign up again.")
        return
      }
      try {
        await sendEmailVerification(auth.currentUser, {
          url: `${window.location.origin}/login`,
        })
        setResendSent(true)
      } catch {
        setVerifyEmailError("Failed to resend. Please wait a minute and try again.")
      }
    }

    return (
      <VerifyEmailScreen
        email={email}
        error={verifyEmailError}
        resent={resendSent}
        onResend={handleResendVerification}
        onBack={() => {
          setIsSignUp(false)
          setResendSent(false)
          setVerifyEmailError("")
          setError("")
          setStep("sign-in")
        }}
      />
    )
  }

  return (
    <AuthCard>
      <AuthHeader
        title="Pablo"
        titleSize="4xl"
        subtitle={
          isSignUp
            ? "Create your account to get started"
            : "HIPAA-compliant therapy session management"
        }
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
            label={isSignUp ? "Create Password" : "Password"}
            type="password"
            autoComplete={isSignUp ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={isSignUp ? "Min 15 characters" : "Password"}
            required
            minLength={isSignUp ? 15 : undefined}
          />

          {isSignUp && (
            <AuthInput
              id="confirmPassword"
              label="Confirm Password"
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Re-enter your password"
              required
              minLength={15}
            />
          )}

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
                setConfirmPassword("")
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
