// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Passkey-first second-factor enrolment for onboarding. Runs the
 * browser WebAuthn registration ceremony via `@simplewebauthn/browser`
 * against the begin/finish endpoints. On success the backend has
 * stamped `mfa_enrolled_at`, so we hand back to the wizard index, which
 * advances past the (grouped) security step.
 *
 * The authenticator-app (TOTP) flow stays available as a fallback link
 * to /onboarding/mfa — for users whose device can't do passkeys, or who
 * just prefer it — but only when the active onboarding surface includes
 * a TOTP step (`showTotpFallback`); a deployment whose auth backend
 * can't enrol TOTP hides the link.
 */

import { useState, useSyncExternalStore } from "react"
import { useRouter } from "next/navigation"
import {
  startRegistration,
  browserSupportsWebAuthn,
  WebAuthnError,
} from "@simplewebauthn/browser"
import { signInWithCustomToken } from "firebase/auth"
import { Fingerprint, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { getFirebaseAuth } from "@/lib/firebase"
import { ApiError } from "@/lib/api/client"
import { beginRegistration, finishRegistration } from "@/lib/api/passkey"
import { trackOnboardingStepCompleted } from "@/lib/analytics/onboarding"
import { RecoveryCodesPanel } from "@/components/onboarding/RecoveryCodesPanel"
import { errorCode } from "@/lib/errors/errorCode"

const BENEFITS = [
  "Sign in with Face ID, Touch ID, or your security key",
  "Nothing to install, no codes to type",
  "Phishing-resistant — there's no shared secret to steal",
]

function enrollErrorMessage(err: unknown): string | null {
  // User dismissed the platform prompt — not worth an error message.
  if (err instanceof WebAuthnError && err.name === "NotAllowedError") return null
  if (err instanceof WebAuthnError && err.name === "InvalidStateError") {
    return "This device already has a passkey for your account."
  }
  if (err instanceof ApiError && err.code === "MFA_REQUIRED") {
    return "Verify an existing passkey before adding another."
  }
  return "Could not create the passkey. Please try again, or use an authenticator app."
}

// Exchange the attestation-minted factor token for a session whose ID token
// carries the second-factor claim. Best-effort: a missing token (older
// backend) or a failed exchange leaves the already-enrolled user to clear MFA
// on their next sign-in, exactly as before the mint existed.
async function upgradeSessionWithFactorToken(customToken: string | null | undefined): Promise<void> {
  if (!customToken) return
  try {
    const credential = await signInWithCustomToken(getFirebaseAuth(), customToken)
    await credential.user.getIdToken(true)
  } catch (err) {
    console.error("passkey factor-token exchange failed", errorCode(err))
  }
}

export function OnboardingPasskeyForm({
  showTotpFallback = true,
}: {
  /** Render the "use an authenticator app instead" link. Off when the
   * active onboarding surface has no TOTP step. */
  showTotpFallback?: boolean
} = {}) {
  const router = useRouter()
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // One-time recovery codes returned on first enrolment — shown once, here,
  // before the wizard is allowed to advance.
  const [codes, setCodes] = useState<string[] | null>(null)

  // WebAuthn support is a client-only check (the API is undefined during
  // SSR). useSyncExternalStore renders `false` on the server and the real
  // value on the client — no setState-in-effect, no hydration mismatch.
  const supported = useSyncExternalStore(
    () => () => {},
    () => browserSupportsWebAuthn(),
    () => false,
  )

  const handleCreate = async () => {
    setSubmitting(true)
    setError(null)
    try {
      const options = await beginRegistration()
      const response = await startRegistration({ optionsJSON: options })
      const result = await finishRegistration(response, null)
      trackOnboardingStepCompleted("passkey")
      // The session that ran the ceremony still holds a first-factor-only
      // token. The backend mints a webauthn factor token from the verified
      // attestation; exchange it and force-refresh the ID token so the new
      // claims are live before we advance — otherwise the wizard lands on the
      // dashboard with a token that fails MFA and 403s every protected route
      // until a sign-out/in. The credential is already enrolled, so a failed
      // exchange must not look like a failed enrolment — fall back to
      // the (pre-fix) sign-in-again behaviour rather than blocking the user.
      await upgradeSessionWithFactorToken(result.custom_token)
      // Backend stamped mfa_enrolled_at. On the first enrolment it also
      // returns one-time recovery codes — show them once before advancing;
      // otherwise let the wizard recompute and move on.
      if (result.backup_codes && result.backup_codes.length > 0) {
        setCodes(result.backup_codes)
        setSubmitting(false)
        return
      }
      router.replace("/onboarding")
    } catch (err) {
      const message = enrollErrorMessage(err)
      if (message) setError(message)
      setSubmitting(false)
    }
  }

  if (codes) {
    return (
      <RecoveryCodesPanel codes={codes} onContinue={() => router.replace("/onboarding")} />
    )
  }

  return (
    <div className="space-y-5">
      <ul className="flex flex-col gap-2">
        {BENEFITS.map((benefit) => (
          <li key={benefit} className="flex items-start gap-2.5 text-sm text-neutral-700">
            <span className="mt-0.5" style={{ color: "var(--brand-panel-accent)" }}>
              ✦
            </span>
            <span>{benefit}</span>
          </li>
        ))}
      </ul>

      {!supported && (
        <p
          className="text-sm rounded-xl px-3.5 py-2.5"
          style={{ background: "var(--color-neutral-100)", color: "var(--color-neutral-600)" }}
        >
          This device or browser can&rsquo;t create a passkey. Use an authenticator
          app instead — you can always add a passkey later from Settings.
        </p>
      )}

      <div className="flex flex-col items-start gap-3 pt-1">
        <Button
          type="button"
          onClick={handleCreate}
          disabled={submitting || !supported}
          className="w-full gap-2 sm:w-auto"
        >
          {submitting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Fingerprint className="h-4 w-4" />
          )}
          {submitting ? "Waiting for your device…" : "Create a passkey"}
        </Button>

        {showTotpFallback && (
          <button
            type="button"
            onClick={() => router.push("/onboarding/mfa")}
            className="text-sm font-medium underline underline-offset-2"
            style={{ color: "var(--color-neutral-600)" }}
          >
            Use an authenticator app instead →
          </button>
        )}
      </div>

      {error && (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
