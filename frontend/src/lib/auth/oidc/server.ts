// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * OIDC implementation of the server-side session accessor (SSR).
 * Reads the Auth.js JWT session cookie via `auth()` and returns the
 * bearer token (id_token) plus the claims the app displays, or null
 * when there is no valid session.
 */

import { auth } from "./config"
import type { ServerSession } from "@/lib/auth/types"

/** Extended session shape — matches what config.ts exposes in `session` callback. */
interface OidcServerSession {
  idToken?: string | null
  error?: string | null
  user?: {
    name?: string | null
    email?: string | null
    image?: string | null
  }
}

export async function getOidcServerSession(): Promise<ServerSession | null> {
  const session = (await auth()) as OidcServerSession | null
  if (!session || !session.idToken || session.error) return null

  return {
    token: session.idToken,
    claims: {
      email: session.user?.email ?? undefined,
      name: session.user?.name ?? undefined,
      picture: session.user?.image ?? undefined,
    },
  }
}
