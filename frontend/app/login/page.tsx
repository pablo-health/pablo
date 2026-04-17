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
  TotpMultiFactorGenerator,
  getMultiFactorResolver,
  type MultiFactorError,
  type MultiFactorResolver,
} from "firebase/auth"
import { getFirebaseAuth } from "@/lib/firebase"
import { useAuth } from "@/lib/auth-context"
import {
  AuthCard,
  AuthDivider,
  AuthFeedback,
  AuthFooter,
  AuthGoogleButton,
  AuthHeader,
  AuthInput,
  AuthOutlineButton,
  AuthPrimaryButton,
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

  const [step, setStep] = useState<LoginStep>("sign-in")

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

  // MFA challenge state
  const [mfaResolver, setMfaResolver] = useState<MultiFactorResolver | null>(null)
  const [totpCode, setTotpCode] = useState("")

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

      const credential = await mfaResolver.resolveSignIn(assertion)
      const idToken = await credential.user.getIdToken()
      await fetch("/api/login", {
        method: "POST",
        headers: { Authorization: `Bearer ${idToken}` },
      })
      router.push("/dashboard")
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
      const idToken = await credential.user.getIdToken()
      await fetch("/api/login", {
        method: "POST",
        headers: { Authorization: `Bearer ${idToken}` },
      })
      router.push("/dashboard")
    } catch (err) {
      const code = (err as { code?: string }).code
      if (code === "auth/multi-factor-auth-required") {
        handleMfaRequired(err as MultiFactorError)
      } else if (
        code === "auth/invalid-credential" ||
        code === "auth/user-not-found" ||
        code === "auth/wrong-password" ||
        code === "auth/user-disabled"
      ) {
        setError("Invalid email or password")
      } else if (code === "auth/too-many-requests") {
        setError("Too many attempts. Please try again later.")
      } else if (code === "auth/network-request-failed") {
        setError("Network error. Please check your connection and try again.")
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
      const result = await signInWithPopup(auth, provider)
      // Sync cookie before navigating (don't race with onIdTokenChanged)
      const idToken = await result.user.getIdToken()
      await fetch("/api/login", {
        method: "POST",
        headers: { Authorization: `Bearer ${idToken}` },
      })
      router.push("/dashboard")
    } catch (err) {
      const code = (err as { code?: string }).code
      const message = (err as { message?: string }).message
      console.error("Google sign-in error:", code)
      if (code === "auth/multi-factor-auth-required") {
        handleMfaRequired(err as MultiFactorError)
      } else if (
        code === "auth/popup-closed-by-user" ||
        code === "auth/cancelled-popup-request"
      ) {
        // Benign popup errors, do nothing
      } else if (code === "auth/popup-blocked") {
        console.log("Popup blocked, falling back to redirect")
        await signInWithRedirect(auth, provider)
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
  if (step === "mfa" && mfaResolver) {
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
              setStep("sign-in")
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
  if (step === "verify-email") {
    const handleResendVerification = async () => {
      const auth = getFirebaseAuth()
      if (!auth.currentUser) {
        setError("Session expired. Please sign up again.")
        return
      }
      try {
        await sendEmailVerification(auth.currentUser, {
          url: `${window.location.origin}/login`,
        })
        setResendSent(true)
      } catch {
        setError("Failed to resend. Please wait a minute and try again.")
      }
    }

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

        {resendSent && (
          <AuthFeedback variant="success">
            Verification email resent. Check your inbox and spam folder.
          </AuthFeedback>
        )}

        <div className="space-y-3">
          <AuthOutlineButton onClick={handleResendVerification} disabled={resendSent}>
            {resendSent ? "Email Resent" : "Resend Verification Email"}
          </AuthOutlineButton>

          <AuthPrimaryButton
            onClick={() => {
              setIsSignUp(false)
              setResendSent(false)
              setError("")
              setStep("sign-in")
            }}
          >
            Back to Sign In
          </AuthPrimaryButton>
        </div>
      </AuthCard>
    )
  }

  // Main Sign-In Screen
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
                setConfirmPassword("")
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
