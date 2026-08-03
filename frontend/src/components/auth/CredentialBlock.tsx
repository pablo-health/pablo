// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Shared credential-acquisition block for the `/login` and `/native-auth`
 * surfaces. Owns everything up to a fully resolved Firebase credential —
 * Google, passkey (plus recovery codes), email/password sign-in and sign-up,
 * forgot password, the MFA challenge, and email verification. What happens
 * AFTER a credential resolves is the host's job, via `onCredential`.
 */

import { useEffect, useState, type ReactNode } from "react"
import {
  signInWithPopup,
  signInWithRedirect,
  signInWithEmailAndPassword,
  signInWithCustomToken,
  sendPasswordResetEmail,
  createUserWithEmailAndPassword,
  sendEmailVerification,
  GoogleAuthProvider,
  getMultiFactorResolver,
  type MultiFactorError,
  type MultiFactorResolver,
  type UserCredential,
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
import { clearFirebaseAuthStorage } from "@/lib/firebaseAuthRecovery"
import { AuthDivider } from "./AuthDivider"
import { AuthFeedback } from "./AuthFeedback"
import { AuthGoogleButton } from "./AuthGoogleButton"
import { AuthInput } from "./AuthInput"
import { AuthLinkButton } from "./AuthLinkButton"
import { AuthOutlineButton } from "./AuthOutlineButton"
import { AuthPrimaryButton } from "./AuthPrimaryButton"
import { MfaChallengeScreen } from "./MfaChallengeScreen"
import { RecoveryCodeScreen } from "./RecoveryCodeScreen"
import { VerifyEmailScreen } from "./VerifyEmailScreen"

// "google" = Google sign-in; "email" = the email/password form; "passkey" =
// WebAuthn sign-in. The tag is just which button to flag, not a credential —
// keep it free of any password/secret value so it stays safe to persist in
// the clear.
export type AuthMethod = "google" | "email" | "passkey"

type CredentialStep = "sign-in" | "mfa" | "recovery-code" | "verify-email"

// Remember how this device last signed in so we can surface a "Last used"
// hint on the matching button. We store only the method tag — never the
// email or password — so a shared workstation reveals nothing about who has
// an account here.
const LAST_AUTH_METHOD_KEY = "pablo:lastAuthMethod"

function readLastAuthMethod(): AuthMethod | null {
  if (typeof window === "undefined") return null
  try {
    const v = window.localStorage.getItem(LAST_AUTH_METHOD_KEY)
    return v === "google" || v === "email" || v === "passkey" ? v : null
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

export interface CredentialBlockProps {
  // Called exactly once, when a credential is fully resolved — including after
  // an MFA challenge or a recovery-code redemption. The host decides what
  // happens next. May be async; the block stays in its loading state until it
  // settles.
  onCredential: (credential: UserCredential, method: AuthMethod) => Promise<void>
  email: string
  onEmailChange: (email: string) => void
  /** Show the "Create account" toggle + sign-up form (default true). */
  allowSignUp?: boolean
  /** Show the "Last used" pill (default false). */
  showLastUsed?: boolean
  /** Show the "Having trouble signing in?" storage-reset link (default false). */
  showAuthReset?: boolean
  /** Host-supplied message surfaced in the block's error slot. */
  notice?: string
  /**
   * Sign-up mode is controllable so a host can key its own chrome (header
   * title, redirect gating, setup-token prefill) off it. Uncontrolled when
   * omitted.
   */
  signUp?: boolean
  onSignUpChange?: (signUp: boolean) => void
  /**
   * The sign-in form renders inside this shell (the host's card, header and
   * footer). The MFA / recovery-code / verify-email steps bring their own
   * full-page card and render bare, replacing the shell entirely.
   */
  renderShell: (form: ReactNode) => ReactNode
}

export function CredentialBlock({
  onCredential,
  email,
  onEmailChange,
  allowSignUp = true,
  showLastUsed = false,
  showAuthReset = false,
  notice,
  signUp,
  onSignUpChange,
  renderShell,
}: CredentialBlockProps) {
  const { passkeysEnabled } = useConfig()

  const [password, setPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const [resetSent, setResetSent] = useState(false)
  const [resendSent, setResendSent] = useState(false)
  const [verifyEmailError, setVerifyEmailError] = useState("")

  const [step, setStep] = useState<CredentialStep>("sign-in")
  const [mfaResolver, setMfaResolver] = useState<MultiFactorResolver | null>(null)

  const [internalSignUp, setInternalSignUp] = useState(false)
  const isSignUp = signUp ?? internalSignUp
  const setIsSignUp = (v: boolean) => {
    setInternalSignUp(v)
    onSignUpChange?.(v)
  }

  // "Last used" hint, and the method that kicked off an in-progress MFA
  // challenge (so we record the right one once the challenge resolves).
  const [lastMethod, setLastMethod] = useState<AuthMethod | null>(null)
  const [pendingMethod, setPendingMethod] = useState<AuthMethod>("email")

  // Only offer passkey sign-in where the browser can actually run the
  // ceremony — resolved client-side after mount to avoid an SSR mismatch.
  const [passkeySupported, setPasskeySupported] = useState(false)
  useEffect(() => {
    setPasskeySupported(passkeysEnabled && browserSupportsWebAuthn())
  }, [passkeysEnabled])

  // A host-supplied notice (forced logout, recovery notice, post-credential
  // failure) lands in the error slot; the next attempt clears it like any
  // other error.
  useEffect(() => {
    if (notice) setError(notice)
  }, [notice])

  // Surface the method this device signed in with last.
  useEffect(() => {
    if (showLastUsed) setLastMethod(readLastAuthMethod())
  }, [showLastUsed])

  const resolveCredential = async (credential: UserCredential, method: AuthMethod) => {
    await onCredential(credential, method)
    // Recording the hint is tied to surfacing it, so a host that never shows
    // the pill leaves the device's record untouched.
    if (showLastUsed) rememberAuthMethod(method)
  }

  const handleMfaRequired = (err: MultiFactorError, method: AuthMethod) => {
    setPendingMethod(method)
    const resolver = getMultiFactorResolver(getFirebaseAuth(), err)
    setMfaResolver(resolver)
    setStep("mfa")
    setError("")
  }

  const handleEmailLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")
    setLoading(true)

    try {
      const credential = await signInWithEmailAndPassword(getFirebaseAuth(), email, password)
      await resolveCredential(credential, "email")
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
      await resolveCredential(result, "google")
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

  const handlePasskeyLogin = async () => {
    setError("")
    setLoading(true)
    try {
      const options = await beginAuthentication()
      const assertion = await startAuthentication({ optionsJSON: options })
      const { custom_token } = await finishAuthentication(assertion)
      // The custom token carries the verified passkey factor (pablo_amr);
      // signing in with it yields an MFA-satisfied session in one step.
      const credential = await signInWithCustomToken(getFirebaseAuth(), custom_token)
      await resolveCredential(credential, "passkey")
    } catch (err) {
      // User dismissed the platform prompt — leave the form untouched.
      if (err instanceof WebAuthnError && err.name === "NotAllowedError") return
      setError("Passkey sign-in failed. Try again, or use your email and password.")
    } finally {
      setLoading(false)
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
        onSuccess={(credential) => resolveCredential(credential, pendingMethod)}
        onCancel={() => {
          setMfaResolver(null)
          setError("")
          setStep("sign-in")
        }}
      />
    )
  }

  if (step === "recovery-code") {
    return (
      <RecoveryCodeScreen
        initialEmail={email}
        onSuccess={(credential) => resolveCredential(credential, "passkey")}
        onCancel={() => {
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

  return renderShell(
    <div className="mt-8 space-y-4">
      <div className="relative">
        <AuthGoogleButton onClick={handleGoogleLogin} />
        {!isSignUp && lastMethod === "google" && <LastUsedPill />}
      </div>

      {!isSignUp && passkeySupported && (
        <div className="space-y-2">
          <div className="relative">
            <AuthOutlineButton
              type="button"
              onClick={handlePasskeyLogin}
              disabled={loading}
              className="w-full flex items-center justify-center gap-3 bg-white border-2 border-neutral-300 text-neutral-700 px-6 py-3.5 rounded-lg font-medium hover:bg-neutral-50 hover:border-primary-400 hover:shadow-md active:scale-[0.98] transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Fingerprint className="h-5 w-5" />
              Sign in with a passkey
            </AuthOutlineButton>
            {lastMethod === "passkey" && <LastUsedPill />}
          </div>
          <div className="text-center">
            <AuthLinkButton
              size="sm"
              onClick={() => {
                setError("")
                setStep("recovery-code")
              }}
            >
              Lost your passkey? Use a recovery code
            </AuthLinkButton>
          </div>
        </div>
      )}

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
          onChange={(e) => onEmailChange(e.target.value)}
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
          {allowSignUp && (
            <AuthLinkButton
              onClick={() => {
                setIsSignUp(!isSignUp)
                setError("")
                setConfirmPassword("")
              }}
            >
              {isSignUp ? "Already have an account?" : "Create account"}
            </AuthLinkButton>
          )}
        </div>

        {showAuthReset && !isSignUp && (
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
  )
}
