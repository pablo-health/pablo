// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Firebase implementation of the `/login` surface — email/password +
 * Google sign-in, sign-up, password reset, email verification, and the
 * TOTP challenge. Rendered through `getAuthSurfaces().LoginScreen`; the
 * `/login` route is a thin shell that picks this per the active provider.
 */

import { useState, useEffect } from "react"
import Image from "next/image"
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
  clearFirebaseAuthStorage,
  consumeRecoveryNotice,
  installAuthRecovery,
} from "@/lib/firebaseAuthRecovery"
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
import { ThemeSwitcher } from "@/components/theme/ThemeSwitcher"

type LoginStep = "sign-in" | "mfa" | "verify-email"

function getUrlParam(name: string): string {
  if (typeof window === "undefined") return ""
  const params = new URLSearchParams(window.location.search)
  return params.get(name) || ""
}

// "google" = Google sign-in; "email" = the email/password form. The tag is
// just which button to flag, not a credential — keep it free of any
// password/secret value so it stays safe to persist in the clear.
type AuthMethod = "google" | "email"

// Remember how this device last signed in so we can surface a "Last used"
// hint on the matching button. We store only the method tag — never the
// email or password — so a shared workstation reveals nothing about who has
// an account here.
const LAST_AUTH_METHOD_KEY = "pablo:lastAuthMethod"

function readLastAuthMethod(): AuthMethod | null {
  if (typeof window === "undefined") return null
  try {
    const v = window.localStorage.getItem(LAST_AUTH_METHOD_KEY)
    return v === "google" || v === "email" ? v : null
  } catch {
    return null
  }
}

function rememberAuthMethod(method: AuthMethod): void {
  try {
    window.localStorage.setItem(LAST_AUTH_METHOD_KEY, method)
  } catch {
    // localStorage blocked (private mode / cookies off) — the hint is
    // best-effort, so a failure here is fine to swallow.
  }
}

function LastUsedPill() {
  return (
    <span className="pointer-events-none absolute -top-2 right-3 rounded-full bg-primary-600 px-2 py-0.5 text-[11px] font-semibold text-white shadow-sm ring-2 ring-card">
      Last used
    </span>
  )
}

export function FirebaseLoginScreen() {
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

  // "Last used" hint, and the method that kicked off an in-progress MFA
  // challenge (so we record the right one once the challenge resolves).
  const [lastMethod, setLastMethod] = useState<AuthMethod | null>(null)
  const [pendingMethod, setPendingMethod] = useState<AuthMethod>("email")

  // Show notice when redirected from idle timeout
  useEffect(() => {
    const reason = getUrlParam("reason")
    if (reason === "idle_timeout") {
      setError("You were signed out due to inactivity.")
      window.history.replaceState({}, "", "/login")
    }
  }, [])

  // Arm the Firebase Auth stuck-state recovery and surface a one-line
  // notice if the last attempt was auto-recovered. THERAPY-n1n6.
  useEffect(() => {
    installAuthRecovery()
    if (consumeRecoveryNotice()) {
      setError(
        "We cleared a stuck sign-in state from a previous attempt. Please try signing in again."
      )
    }
  }, [])

  // Surface the method this device signed in with last.
  useEffect(() => {
    setLastMethod(readLastAuthMethod())
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
      .catch((err) => {
        // Token expired or invalid — user types email manually. Logged so
        // backend exchange failures (e.g. the tenant-isolation trigger fires
        // on /api/auth/exchange-setup-token) surface in the user's console.
        console.error("exchange-setup-token failed:", err)
      })
  }, [])

  // Redirect to dashboard when already authenticated (but not during signup flow)
  useEffect(() => {
    if (user && !authLoading && step !== "verify-email" && !isSignUp) {
      router.push("/dashboard")
    }
  }, [user, authLoading, router, step, isSignUp])

  const handleMfaRequired = (err: MultiFactorError, method: AuthMethod) => {
    setPendingMethod(method)
    const resolver = getMultiFactorResolver(getFirebaseAuth(), err)
    setMfaResolver(resolver)
    setStep("mfa")
    setError("")
  }

  const finishLogin = async (credential: UserCredential, method: AuthMethod) => {
    const idToken = await credential.user.getIdToken()
    await fetch("/api/login", {
      method: "POST",
      headers: { Authorization: `Bearer ${idToken}` },
    })
    rememberAuthMethod(method)
    router.push("/dashboard")
  }

  const handleEmailLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")
    setLoading(true)

    try {
      const credential = await signInWithEmailAndPassword(getFirebaseAuth(), email, password)
      await finishLogin(credential, "email")
    } catch (err) {
      const outcome = firebaseAuthErrorOutcome(err, "sign-in")
      if (outcome.kind === "mfa-required") {
        handleMfaRequired(err as MultiFactorError, "email")
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
      await finishLogin(result, "google")
    } catch (err) {
      const outcome = firebaseAuthErrorOutcome(err, "google")
      if (outcome.kind === "mfa-required") {
        handleMfaRequired(err as MultiFactorError, "google")
      } else if (outcome.kind === "popup-blocked") {
        console.log("Popup blocked, falling back to redirect")
        await signInWithRedirect(auth, provider)
      } else if (outcome.kind === "message") {
        setError(outcome.message)
      }
    }
  }

  const handleAuthReset = async () => {
    await clearFirebaseAuthStorage()
    window.location.reload()
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
        onSuccess={(credential) => finishLogin(credential, pendingMethod)}
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
      } catch (err) {
        console.error("sendEmailVerification failed:", err)
        const outcome = firebaseAuthErrorOutcome(err, "verify-email")
        if (outcome.kind === "message") setVerifyEmailError(outcome.message)
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
    <AuthCard brandPanel={<LoginBrandPanel />}>
      <div className="mb-6 flex flex-col items-center gap-2 lg:hidden">
        <div className="flex items-center gap-2.5">
          <Image src="/pablo-login.webp" alt="" width={44} height={44} className="object-contain" />
          <span className="font-display text-2xl font-bold text-primary-600">Pablo</span>
        </div>
        <span className="text-xs text-neutral-500">AI documentation for mental health clinicians</span>
      </div>

      <AuthHeader
        title={isSignUp ? "Create your account" : "Welcome back"}
        titleSize="4xl"
        titleColor="foreground"
      />

      <div className="mt-8 space-y-4">
        <div className="relative">
          <AuthGoogleButton onClick={handleGoogleLogin} />
          {!isSignUp && lastMethod === "google" && <LastUsedPill />}
        </div>

        <AuthDivider />

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

          <div className="relative">
            <AuthPrimaryButton type="submit" disabled={loading}>
              {loading
                ? isSignUp
                  ? "Creating account..."
                  : "Signing in..."
                : isSignUp
                  ? "Create Account"
                  : "Sign In"}
            </AuthPrimaryButton>
            {!isSignUp && lastMethod === "email" && <LastUsedPill />}
          </div>

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

          {!isSignUp && (
            <div className="text-center">
              <AuthLinkButton size="sm" onClick={handleAuthReset}>
                Having trouble signing in?
              </AuthLinkButton>
            </div>
          )}
        </form>

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

      {/* Brand panel (with the theme picker) is hidden on mobile, so offer it here. */}
      <div className="mt-6 flex justify-center lg:hidden">
        <div className="flex flex-col items-center gap-2">
          <span className="text-[0.6875rem] font-semibold uppercase tracking-[0.09em] text-neutral-500">
            Theme
          </span>
          <ThemeSwitcher />
        </div>
      </div>
    </AuthCard>
  )
}

function LoginBrandPanel() {
  const points = [
    "AI drafts your SOAP notes from the session for you to review, edit, and finalize",
    "Chat right on a patient's chart to get answers in context",
    "Compliance items and notes to finalize, tracked in one place",
    "HIPAA-compliant by design",
  ]
  return (
    <>
      <div>
        <span
          className="mb-2.5 block text-xs font-semibold uppercase tracking-[0.12em]"
          style={{ color: "var(--brand-panel-accent)" }}
        >
          For mental health clinicians
        </span>
        <span className="font-display text-3xl font-bold">Pablo</span>
        <p
          className="mt-6 max-w-xs font-display text-2xl leading-snug"
          style={{ color: "var(--brand-panel-fg)" }}
        >
          Let AI carry the documentation, so your evenings and weekends are yours again.
        </p>
        <div className="mt-8 flex flex-col gap-2">
          <span className="text-[0.6875rem] font-semibold uppercase tracking-[0.09em] text-brand-panel-muted">
            Theme
          </span>
          <ThemeSwitcher />
        </div>
      </div>
      <div className="flex flex-1 items-center justify-center py-8">
        <Image
          src="/pablo-login.webp"
          alt="Pablo, the friendly bear in a tie"
          width={240}
          height={240}
          priority
          className="drop-shadow-2xl"
        />
      </div>
      <ul className="space-y-3 text-sm">
        {points.map((point) => (
          <li key={point} className="flex items-start gap-3">
            <span style={{ color: "var(--brand-panel-accent)" }}>✦</span>
            <span style={{ color: "var(--brand-panel-muted)" }}>{point}</span>
          </li>
        ))}
      </ul>
    </>
  )
}
