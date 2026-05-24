/**
 * Coarse smoke spec that lives alongside the deploy pipeline's curl-
 * based /api/health check. Replaces it with a Playwright-driven check
 * so the rest of the runtime (Cloud Run Job, artifact upload, JUnit
 * report) exercises end-to-end with at least one assertion.
 */
import { expect, test } from "@playwright/test";

test.describe("Deployment smoke", () => {
  test("backend /api/health reports a git_sha", async ({
    request,
    baseURL,
  }) => {
    expect(baseURL, "baseURL must be set by the runner").toBeTruthy();
    const resp = await request.get(`${baseURL}/api/health`);
    expect(resp.ok(), `expected /api/health 2xx, got ${resp.status()}`).toBe(
      true,
    );
    const body = (await resp.json()) as { git_sha?: string; status?: string };
    expect(body.git_sha, "/api/health missing git_sha").toBeTruthy();
  });

  test("frontend root renders", async ({ page, baseURL }) => {
    const resp = await page.goto(`${baseURL}/`);
    expect(resp?.ok(), `expected GET / 2xx, got ${resp?.status()}`).toBe(true);
    await expect(page.locator("body")).toBeVisible();
  });
});
