// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Selects the active provider's auth UI surfaces (login, native-auth,
 * auth-action, MFA enrollment). The `/login`, `/native-auth`,
 * `/auth/action`, and `/mfa-enrollment` routes are thin shells that
 * render whatever this returns.
 */

import { type AuthSurfaces } from "./types"
import { activeAuthProviderId } from "./provider"
import { firebaseAuthSurfaces } from "./firebase/surfaces"

export function getAuthSurfaces(): AuthSurfaces {
  const id = activeAuthProviderId()
  switch (id) {
    case "firebase":
      return firebaseAuthSurfaces
    case "oidc":
      // Wired in step 5 (login → signIn("keycloak"); MFA on Keycloak's
      // pages → no-op surface). Dev stays on `firebase` until step 7.
      throw new Error("OIDC auth surfaces are not wired yet (step 5).")
    default:
      throw new Error(`Unknown NEXT_PUBLIC_AUTH_PROVIDER: ${id}`)
  }
}
