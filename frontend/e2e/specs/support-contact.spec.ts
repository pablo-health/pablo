// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * When the dashboard can't load, the clinician can see where to write.
 *
 * A panel whose load failed used to show its empty state ("No sessions
 * today") with no hint anything was wrong and nowhere to turn. Now it says
 * what didn't load, offers a retry, and names the deployment's support
 * address. The sidebar's Help shows the same address at any time.
 *
 * The stack sets SUPPORT_EMAIL on the frontend container
 * (docker-compose.e2e.yml), so this walks the real path: container env, the
 * config route, the page. The unset case (nothing rendered) is a unit test.
 */

import { expect, test } from "../fixtures/auth"

const SUPPORT_EMAIL = "help@practice.example"

// 503 is what the backend sends while it cannot serve a request, and the one
// 5xx the server-error guard expects specs to see on purpose.
const UNAVAILABLE = {
  detail: { error: { code: "SERVICE_UNAVAILABLE", message: "Service unavailable", details: {} } },
}

test("a dashboard panel that can't load names the support address", async ({
  signedInPage: page,
}) => {
  await page.route("**/api/dashboard/summary**", (route) =>
    route.fulfill({ status: 503, json: UNAVAILABLE }),
  )

  await page.goto("/dashboard")

  // The query retries before it gives up, so allow for the backoff.
  const failure = page.getByRole("alert").filter({ hasText: "Today’s sessions didn’t load." })
  await expect(failure).toBeVisible({ timeout: 20_000 })
  await expect(failure.getByRole("button", { name: "Try again" })).toBeVisible()
  await expect(failure).toContainText(`Still not loading? Email ${SUPPORT_EMAIL}.`)
  await expect(failure.getByRole("link", { name: SUPPORT_EMAIL })).toHaveAttribute(
    "href",
    `mailto:${SUPPORT_EMAIL}`,
  )
  await expect(page.getByText(/no sessions today/i)).toHaveCount(0)
})

test("Help in the sidebar shows the support address", async ({ signedInPage: page }) => {
  await page.goto("/dashboard")

  const nav = page.getByRole("navigation", { name: "Main navigation" })
  await nav.getByRole("button", { name: "Help" }).click()

  await expect(page.getByRole("link", { name: SUPPORT_EMAIL })).toHaveAttribute(
    "href",
    `mailto:${SUPPORT_EMAIL}`,
  )
})
