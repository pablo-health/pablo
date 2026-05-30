// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Server-side (SSR) session accessor. Selects the active provider and
 * returns the bearer token plus id-token claims, or null when there is no
 * valid session. Used by the dashboard layout/page and the MFA page.
 */

import { DEFAULT_AUTH_PROVIDER, type AuthProviderId, type ServerSession } from "./types"
import { getFirebaseServerSession } from "./firebase/server"

function activeAuthProviderId(): AuthProviderId {
  return (process.env.NEXT_PUBLIC_AUTH_PROVIDER as AuthProviderId | undefined) || DEFAULT_AUTH_PROVIDER
}

export function getServerSession(): Promise<ServerSession | null> {
  const id = activeAuthProviderId()
  switch (id) {
    case "firebase":
      return getFirebaseServerSession()
    case "oidc":
      // Wired in step 4 (Auth.js `auth()` → session id_token). Dev stays
      // on `firebase` until step 7.
      throw new Error("OIDC server auth provider is not wired yet (step 4).")
    default:
      throw new Error(`Unknown NEXT_PUBLIC_AUTH_PROVIDER: ${id}`)
  }
}
