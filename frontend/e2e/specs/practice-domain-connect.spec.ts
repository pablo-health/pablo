// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One-click DNS setup on Settings > Domains, through Domain Connect.
 *
 * This stack does not turn one-click setup on: a real link has to be signed
 * by the deployment's key, and the provider at the other end is a real DNS
 * company. So the offer is written into the connect answer on its way to the
 * browser, and what is proven is that the page turns it into a link to the
 * provider in the right section. The signing, the discovery against captured
 * provider answers, and which practices get a link are covered in
 * backend/tests/test_domain_connect_*.py.
 *
 * The return path runs for real: arriving with a `state` this deployment did
 * not issue is refused by the server, and the page says so rather than
 * claiming anything changed.
 */

import { test, expect } from "../fixtures/auth"

const SETTINGS_PATH = "/dashboard/settings/domains"
const APEX = "e2e-practice.example"
const APPLY_URL =
  "https://dns-provider.e2e-stack.example/v2/domainTemplates/providers/provider.example/services/practice-domain/apply?domain=e2e-practice.example"

test.describe("One-click DNS setup", () => {
  test("the owner is offered the DNS provider's setup where the server signed a link", async ({
    signedInPage: page,
  }) => {
    await page.route("**/api/practice/domains/connect", async (route) => {
      await route.fulfill({
        json: {
          domains: [
            {
              apex: APEX,
              offers: [
                {
                  service_id: "practice-domain",
                  purpose: "portal",
                  supported: true,
                  provider_name: "Example DNS",
                  url: APPLY_URL,
                  reason: null,
                },
                {
                  service_id: "practice-website",
                  purpose: "site",
                  supported: null,
                  provider_name: null,
                  url: null,
                  reason: "hosts_differ",
                },
              ],
            },
          ],
        },
      })
    })

    await page.goto(SETTINGS_PATH)
    const offer = page.getByTestId(`domain-connect-${APEX}`)
    await expect(offer).toContainText(`Example DNS can add the records for ${APEX} for you.`)
    await expect(offer.getByRole("link", { name: "Set up with Example DNS" })).toHaveAttribute("href", APPLY_URL)
    await expect(page.getByRole("link", { name: /Set up with/ })).toHaveCount(1)
  })

  test("coming back with a state this deployment did not issue is refused", async ({ signedInPage: page }) => {
    await page.goto(`${SETTINGS_PATH}?state=not-issued-here`)

    await expect(page.getByTestId("domain-connect-error")).toHaveText("That link has expired. Start again from this page.")
    await expect(page).toHaveURL(new RegExp(`${SETTINGS_PATH}$`))
    await expect(page.getByTestId("domain-connect-result")).toHaveCount(0)
  })
})
