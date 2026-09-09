// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A token for an account that no longer exists is rejected, not a server error.
 *
 * The backend verifies id tokens with revocation checking on, which makes the
 * Admin SDK fetch the user record as part of verification. When the account has
 * since been deleted that fetch raises, and for a while nothing caught it — so
 * a client holding such a token was told the server had failed rather than that
 * it needed to sign in again.
 *
 * The token in that state is well-formed and unexpired; it simply refers to
 * nobody. That is a 401 like every other rejection on this path.
 */

import { expect, test } from "@playwright/test"
import { createEmulatorUser, deleteEmulatorUser, signInWithPassword } from "../fixtures/api"
import { BACKEND_URL } from "../fixtures/stack"

test("a token for a deleted account is rejected with 401, not 500", async () => {
  const email = `deleted-${Date.now()}@example.invalid`
  const password = "Correct-Horse-Battery-9"

  await createEmulatorUser(email, password)
  const idToken = await signInWithPassword(email, password)

  // The account goes away; the token in hand does not.
  await deleteEmulatorUser(idToken)

  const response = await fetch(`${BACKEND_URL}/api/users/me/status`, {
    headers: { Authorization: `Bearer ${idToken}` },
  })

  expect(
    response.status,
    "a token that refers to nobody must be rejected, not raised on",
  ).toBe(401)

  const body = (await response.json()) as { error?: { code?: string } }
  expect(
    body.error?.code,
    "the client needs to know to sign in again, which a generic error does not say",
  ).toBe("USER_NOT_FOUND")
})
