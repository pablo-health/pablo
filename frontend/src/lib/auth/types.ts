// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Pluggable frontend auth provider — shared contracts.
 *
 * The app depends on these interfaces, never on a concrete auth SDK. A
 * single implementation is selected at the composition root by
 * `NEXT_PUBLIC_AUTH_PROVIDER` (default `firebase`). This mirrors the
 * backend, which dispatches token verification on the JWT issuer and can
 * run providers side by side.
 */

import type { ComponentType } from "react"

export type AuthProviderId = "firebase" | "oidc"

/** Provider used when `NEXT_PUBLIC_AUTH_PROVIDER` is unset. */
export const DEFAULT_AUTH_PROVIDER: AuthProviderId = "firebase"

/**
 * Provider-neutral view of the signed-in user — the minimal shape the
 * shared app consumes. Provider-specific surfaces (e.g. Firebase TOTP
 * enrollment) reach for their underlying SDK user directly instead.
 */
export interface AuthUser {
  uid: string
  email: string | null
  displayName: string | null
  photoURL: string | null
}

/** Live auth state exposed to React through the `AuthProvider` context. */
export interface AuthState {
  user: AuthUser | null
  loading: boolean
}

/**
 * Client-side auth contract the React app depends on. Exactly one
 * concrete implementation is active per deployment.
 */
export interface ClientAuthProvider {
  readonly id: AuthProviderId
  /**
   * React hook returning live auth state. Owns the provider's own React
   * integration (SDK init, state subscription, any server-cookie sync).
   * Safe to call unconditionally: the active provider is fixed for the
   * lifetime of the app.
   */
  useAuthState(): AuthState
  /**
   * The current user's id token for the `Authorization` header, or null
   * when signed out / not yet initialized. `forceRefresh` bypasses any
   * cached token (used by the retry-on-401 path).
   */
  getIdToken(forceRefresh?: boolean): Promise<string | null>
  /**
   * Sign out fully: clear the client SDK session AND the server session
   * cookie. Callers handle their own post-logout redirect.
   */
  signOut(): Promise<void>
}

/** Props accepted by a provider's MFA-enrollment surface. */
export interface MfaEnrollmentFormProps {
  /**
   * Override the post-enrollment redirect. The SaaS overlay's
   * wizard-chrome variant embeds the form directly and passes this
   * instead of putting the destination in the URL.
   */
  returnTo?: string
}

/**
 * The auth UI surfaces an app route renders through the active provider.
 * Kept separate from {@link ClientAuthProvider} so the (heavy) login/MFA
 * components are only pulled into the route bundles that render them, not
 * into every module that needs a token or sign-out.
 *
 * Firebase renders its existing custom screens. OIDC renders thin
 * redirect shells — Keycloak hosts login, TOTP/passkey enrollment, and
 * email actions on its own pages.
 */
export interface AuthSurfaces {
  readonly id: AuthProviderId
  /** `/login` — interactive sign-in. */
  LoginScreen: ComponentType
  /** `/native-auth` — companion-app (desktop) sign-in handoff. */
  NativeAuthScreen: ComponentType
  /** `/auth/action` — email-link action handler (verify / reset / etc.). */
  AuthActionScreen: ComponentType
  /** `/mfa-enrollment` — second-factor enrollment. */
  MfaEnrollmentForm: ComponentType<MfaEnrollmentFormProps>
}

/** Id-token claims the shared app reads server-side (SSR). */
export interface ServerSessionClaims {
  email?: string
  name?: string
  picture?: string
}

/** A resolved server-side session: the bearer token plus id-token claims. */
export interface ServerSession {
  token: string
  claims: ServerSessionClaims
}
