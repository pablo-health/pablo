// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { QueryClient } from "@tanstack/react-query"
import { getClientAuthProvider } from "@/lib/auth/provider"

/**
 * Shared body for every soft sign-out (client-side navigation to /login,
 * as opposed to the hard `window.location.assign` boot in
 * handleTerminalAuthLogout, which reloads the page and drops the cache for
 * free). Without clearing the query cache here, a soft-signed-out browser
 * that logs back in as someone else can still serve the previous account's
 * cached query results until each one happens to refetch.
 */
export async function signOutAndClear(
  queryClient: QueryClient,
  router: { push: (href: string) => void },
  destination: string,
): Promise<void> {
  try {
    await getClientAuthProvider().signOut()
  } catch {
    // Provider not initialized (dev mode) — still clear and redirect.
  }
  queryClient.clear()
  router.push(destination)
}
