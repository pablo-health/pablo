// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Composition root for the client-side auth provider. Selects the active
 * implementation from `NEXT_PUBLIC_AUTH_PROVIDER` (default `firebase`),
 * mirroring the backend's issuer-based dispatch. The rest of the app
 * depends on this — never on a concrete auth SDK.
 */

import { DEFAULT_AUTH_PROVIDER, type AuthProviderId, type ClientAuthProvider } from "./types"
import { firebaseClientProvider } from "./firebase/client"
import { oidcClientProvider } from "./oidc/client"

export function activeAuthProviderId(): AuthProviderId {
  return (process.env.NEXT_PUBLIC_AUTH_PROVIDER as AuthProviderId | undefined) || DEFAULT_AUTH_PROVIDER
}

export function getClientAuthProvider(): ClientAuthProvider {
  const id = activeAuthProviderId()
  switch (id) {
    case "firebase":
      return firebaseClientProvider
    case "oidc":
      return oidcClientProvider
    default:
      throw new Error(`Unknown NEXT_PUBLIC_AUTH_PROVIDER: ${id}`)
  }
}
