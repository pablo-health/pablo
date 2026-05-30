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
import { oidcAuthSurfaces } from "./oidc/surfaces"

export function getAuthSurfaces(): AuthSurfaces {
  const id = activeAuthProviderId()
  switch (id) {
    case "firebase":
      return firebaseAuthSurfaces
    case "oidc":
      return oidcAuthSurfaces
    default:
      throw new Error(`Unknown NEXT_PUBLIC_AUTH_PROVIDER: ${id}`)
  }
}
