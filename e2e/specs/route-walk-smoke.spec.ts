/**
 * Post-deploy route-walk smoke.
 *
 * Walks every top-level authenticated route as the pinned E2E user
 * and asserts each one renders without throwing. Companion to the
 * chart-render smoke — that spec exercises a single route deeply; this
 * one covers breadth.
 *
 * Motivation (2026-05-22): pablo#253 (ToastProvider) and pablo#255
 * (chat eager-drain) both shipped a regression that a curl-based
 * health check could not catch. A 30-second route walk would have
 * failed the deploy on #253 since the chart crashed on render.
 *
 * What this proves:
 *   1. Every top-level route in the sidebar nav, plus patient detail
 *      and patient notes, renders to its primary heading.
 *   2. No console.error or uncaught exception fires across the whole
 *      walk (one guard for the full traversal — a regression on any
 *      single route flags the spec).
 *
 * Deliberately NOT in scope:
 *   - Data assertions (counts, list contents) — other specs cover.
 *   - Deep tab/sub-route walks — would balloon runtime.
 *   - Form interaction — separate per-feature specs.
 *
 * Budget: aim for ~30s total. Each visit is goto → waitForAppReady →
 * one heading assertion (~3s). Patient detail needs an API-created
 * patient as a side scenario.
 */
import { expect, test } from "../fixtures/auth";
import { attachConsoleGuard } from "../fixtures/consoleGuard";
import { waitForAppReady } from "../flows/onboarding";
import { scenarios } from "../fixtures/scenarios";

test("every top-level route renders cleanly @smoke", async ({
  onboardedUser,
  signedInPage,
}) => {
  const page = signedInPage;
  const guard = attachConsoleGuard(page);

  try {
    // One patient is needed for /patients/[id] and /patients/[id]/notes.
    const patient = await scenarios.givePatient(onboardedUser);

    type Stop = { path: string; assert: () => Promise<void> };

    const stops: Stop[] = [
      {
        path: "/dashboard",
        // Greeting is time-of-day-dependent — anchor on the Pablo
        // sidebar brand, which is in the layout shell and present on
        // every authenticated route.
        assert: async () => {
          await expect(
            page.getByRole("heading", { name: /^Pablo$/ }),
            "Pablo sidebar brand visible",
          ).toBeVisible({ timeout: 10_000 });
        },
      },
      {
        path: "/dashboard/calendar",
        assert: async () => {
          await expect(
            page.getByRole("heading", { name: /^Calendar$/ }),
          ).toBeVisible({ timeout: 10_000 });
        },
      },
      {
        path: "/dashboard/patients",
        assert: async () => {
          await expect(
            page.getByRole("heading", { name: /^Patients$/ }),
          ).toBeVisible({ timeout: 10_000 });
        },
      },
      {
        path: `/dashboard/patients/${patient.id}`,
        assert: async () => {
          await expect(
            page.getByRole("heading", {
              name: `${patient.first_name} ${patient.last_name}`,
            }),
          ).toBeVisible({ timeout: 15_000 });
        },
      },
      {
        path: `/dashboard/patients/${patient.id}/notes`,
        assert: async () => {
          await expect(
            page.getByRole("heading", { name: /^Notes$/ }),
          ).toBeVisible({ timeout: 10_000 });
        },
      },
      {
        path: "/dashboard/sessions",
        assert: async () => {
          await expect(
            page.getByRole("heading", { name: /^Sessions$/ }),
          ).toBeVisible({ timeout: 10_000 });
        },
      },
      {
        path: "/dashboard/settings",
        assert: async () => {
          await expect(
            page.getByRole("heading", { name: /^Settings$/ }),
          ).toBeVisible({ timeout: 10_000 });
        },
      },
    ];

    for (const stop of stops) {
      await test.step(`visit ${stop.path}`, async () => {
        await page.goto(stop.path);
        await waitForAppReady(page);
        await stop.assert();
      });
    }

    // Single check at the end covers the entire walk — a regression
    // on any route surfaces here. The trace identifies which step
    // dirtied the guard.
    expect(
      guard.pageErrors.map((e) => e.message),
      "no uncaught page exceptions across the route walk",
    ).toEqual([]);
    expect(
      guard.errors,
      "no console.error messages across the route walk",
    ).toEqual([]);
  } finally {
    guard.dispose();
  }
});
