// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The OIDC provider's auth UI surfaces, assembled into the
 * {@link AuthSurfaces} registry the route shells dispatch through.
 *
 * All four surfaces are thin shells: Keycloak hosts login, TOTP/passkey
 * enrollment, and email actions on its own pages.
 */

import type { AuthSurfaces } from "@/lib/auth/types"
import { OidcLoginScreen } from "./LoginScreen"
import { OidcNativeAuthScreen } from "./NativeAuthScreen"
import { OidcAuthActionScreen } from "./AuthActionScreen"
import { OidcMfaEnrollmentForm } from "./MfaEnrollmentForm"

export const oidcAuthSurfaces: AuthSurfaces = {
  id: "oidc",
  LoginScreen: OidcLoginScreen,
  NativeAuthScreen: OidcNativeAuthScreen,
  AuthActionScreen: OidcAuthActionScreen,
  MfaEnrollmentForm: OidcMfaEnrollmentForm,
}
