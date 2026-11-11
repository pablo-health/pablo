// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { getClientAuthProvider } from "@/lib/auth/provider"
import { logoutPath } from "@/lib/auth/paths"

/**
 * How long the provider sign-out may take before the navigation goes ahead
 * without it. The navigation is the part that must happen: the user clicked
 * Sign out, and the page they are leaving may already be unable to reach
 * the API at all.
 */
export const HARD_SIGN_OUT_BUDGET_MS = 3000

/**
 * The user's own sign-out: end the client session, then navigate to the
 * logout path, which clears the session cookies and lands on /login in one
 * response.
 *
 * A soft sign-out (provider call, then a client-side push to /login) needs
 * that call to succeed. When the session has gone stale - the cookie still
 * verifies but the backend refuses the token - the call can hang, and if the
 * cookie clear never lands the push to /login bounces straight back into the
 * dead session. Here the provider call is best-effort and bounded, and the
 * navigation does the clearing itself, so nothing a dead session can do
 * keeps the user on the page.
 *
 * The OIDC provider's sign-out ends in a navigation of its own (the identity
 * provider's end-session URL), so it keeps that; only the cookie-session
 * provider is sent through the logout path.
 */
export async function hardSignOut(): Promise<void> {
  try {
    const provider = getClientAuthProvider()
    if (provider.id === "oidc") {
      await provider.signOut()
      return
    }
    // `wipePersisted`: the SDK must not re-hydrate the old session from
    // storage on the next load (bfcache / iOS Safari) - same reasoning as
    // the terminal-auth boot in the API client.
    await Promise.race([
      provider.signOut({ wipePersisted: true }),
      new Promise((resolve) => setTimeout(resolve, HARD_SIGN_OUT_BUDGET_MS)),
    ])
  } catch {
    // Provider not initialized, or sign-out failed - the navigation below
    // still clears the cookies.
  }
  window.location.assign(logoutPath)
}
