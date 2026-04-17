// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

export type AuthErrorVariant = "sign-in" | "sign-up" | "google" | "mfa"

/**
 * Sentinel values for outcomes that aren't simple error strings.
 * - "noop" means the caller should silently ignore (e.g. user dismissed popup).
 * - "mfa-required" means the caller should enter the MFA challenge flow.
 * - "popup-blocked" means the caller should fall back to redirect sign-in
 *   (or display the returned message, depending on platform).
 */
export type AuthErrorOutcome =
  | { kind: "message"; message: string }
  | { kind: "noop" }
  | { kind: "mfa-required" }
  | { kind: "popup-blocked" }

function extractCodeMessage(err: unknown): { code: string | undefined; message: string | undefined } {
  const e = err as { code?: string; message?: string }
  return { code: e?.code, message: e?.message }
}

export function firebaseAuthErrorOutcome(err: unknown, variant: AuthErrorVariant): AuthErrorOutcome {
  const { code, message } = extractCodeMessage(err)

  if (code === "auth/multi-factor-auth-required") {
    return { kind: "mfa-required" }
  }

  if (code === "auth/popup-closed-by-user" || code === "auth/cancelled-popup-request") {
    return { kind: "noop" }
  }

  if (code === "auth/popup-blocked") {
    return { kind: "popup-blocked" }
  }

  if (code === "auth/blocking-function-error-response") {
    const fallback = variant === "sign-up" ? "Sign-up blocked by administrator." : "Sign-in blocked by administrator."
    return { kind: "message", message: message || fallback }
  }

  if (variant === "sign-up") {
    if (code === "auth/email-already-in-use") {
      return { kind: "message", message: "An account with this email already exists. Try signing in." }
    }
    if (code === "auth/weak-password" || code === "auth/password-does-not-meet-requirements") {
      return { kind: "message", message: "Password must be at least 15 characters." }
    }
    if (code === "auth/admin-restricted-operation") {
      return { kind: "message", message: "Sign-up is restricted. Please contact your administrator." }
    }
    return { kind: "message", message: `Sign-up failed (${code || "unknown"}). Please try again.` }
  }

  if (variant === "mfa") {
    if (code === "auth/invalid-verification-code") {
      return { kind: "message", message: "Invalid verification code. Please try again." }
    }
    return { kind: "message", message: "MFA verification failed. Please try again." }
  }

  if (
    code === "auth/invalid-credential" ||
    code === "auth/user-not-found" ||
    code === "auth/wrong-password" ||
    code === "auth/user-disabled"
  ) {
    return { kind: "message", message: "Invalid email or password" }
  }
  if (code === "auth/too-many-requests") {
    return { kind: "message", message: "Too many attempts. Please try again later." }
  }
  if (code === "auth/network-request-failed") {
    return { kind: "message", message: "Network error. Please check your connection and try again." }
  }

  if (variant === "google") {
    return { kind: "message", message: `Google sign-in failed (${code || "unknown"}). Please try again.` }
  }

  return { kind: "message", message: "Login failed. Please try again." }
}
