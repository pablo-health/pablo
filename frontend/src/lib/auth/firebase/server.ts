// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Firebase implementation of the server-side session accessor (SSR).
 * Reads the ``next-firebase-auth-edge`` session cookie and returns the
 * bearer token plus the id-token claims the app displays.
 */

import { cookies } from "next/headers"
import { getTokens } from "next-firebase-auth-edge"

import { authConfig } from "@/lib/auth-config"
import type { ServerSession } from "@/lib/auth/types"

export async function getFirebaseServerSession(): Promise<ServerSession | null> {
  const tokens = await getTokens(await cookies(), authConfig)
  if (!tokens) return null

  const { token, decodedToken } = tokens
  return {
    token,
    claims: {
      email: decodedToken.email,
      name: decodedToken.name,
      picture: decodedToken.picture,
    },
  }
}
