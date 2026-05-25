import { expect, test } from "@playwright/test";

/**
 * SPA smoke test. Confirms Playwright can reach the configured base URL,
 * the SPA finishes loading its runtime config, and an unauthenticated
 * visit redirects to the login page. Complements health-smoke.spec.ts
 * (which checks the API health endpoint) by exercising the frontend.
 */
test("base URL responds", async ({ page }) => {
  const response = await page.goto("/");
  expect(response, "page.goto should return a response").not.toBeNull();
  expect(response!.status(), `status from ${page.url()}`).toBeLessThan(500);
});

test("SPA finishes loading config and shows the login page", async ({
  page,
}) => {
  await page.goto("/");
  // Wait until the SPA finishes loading config and replaces the spinner.
  await expect(page.getByText("Loading configuration")).toBeHidden({
    timeout: 15_000,
  });
  // Should land on the login page (unauthed redirect).
  await expect(page).toHaveURL(/\/login/);
});
