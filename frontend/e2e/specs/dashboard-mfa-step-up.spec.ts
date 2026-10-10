// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A dashboard the browser can't load for want of a passkey goes to step-up.
 *
 * The dashboard layout decides who gets in from the server session cookie,
 * but the page's own requests carry the browser's Firebase token. The two can
 * disagree: a returning session passed the layout on a cookie that held the
 * passkey-upgraded token while the browser sent one without it. The backend
 * then refused every panel with 403 MFA_REQUIRED, and the page showed a
 * dashboard full of failures with nothing to click that would fix it.
 *
 * Here the signed-in session is real, so the layout lets it through exactly as
 * it did then, and the browser's reads are answered the way the backend
 * answers a token without the second factor. The page has to land on the
 * passkey step-up screen, not stay on a broken dashboard.
 */

import { expect, test } from "../fixtures/auth"

// The envelope the backend sends: `HTTPException(detail={"error": ...})`.
const MFA_REQUIRED = {
  detail: {
    error: {
      code: "MFA_REQUIRED",
      message: "Multi-factor authentication is required",
      details: {},
    },
  },
}

test("a dashboard whose loads need the passkey sends the user to step-up", async ({
  signedInPage: page,
}) => {
  await page.route("**/api/dashboard/summary**", (route) =>
    route.fulfill({ status: 403, json: MFA_REQUIRED }),
  )

  await page.goto("/dashboard")

  await expect(page).toHaveURL(/\/mfa-step-up$/)
  await expect(page.getByRole("heading", { name: "Confirm it’s you" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Use passkey" })).toBeVisible()
})
