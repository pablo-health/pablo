// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Route-protection middleware selector. The root `middleware.ts` delegates
 * here; this picks the active provider's edge middleware.
 */

import type { NextRequest } from "next/server"
import { DEFAULT_AUTH_PROVIDER, type AuthProviderId } from "./types"
import firebaseAuthMiddleware from "./firebase/middleware"
import oidcAuthMiddleware from "./oidc/middleware"

function activeAuthProviderId(): AuthProviderId {
  return (process.env.NEXT_PUBLIC_AUTH_PROVIDER as AuthProviderId | undefined) || DEFAULT_AUTH_PROVIDER
}

export function authProviderMiddleware(request: NextRequest) {
  const id = activeAuthProviderId()
  switch (id) {
    case "firebase":
      return firebaseAuthMiddleware(request)
    case "oidc":
      return oidcAuthMiddleware(request)
    default:
      throw new Error(`Unknown NEXT_PUBLIC_AUTH_PROVIDER: ${id}`)
  }
}
