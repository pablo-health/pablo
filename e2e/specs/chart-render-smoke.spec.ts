/**
 * Patient-chart render smoke.
 *
 * Catches the class of regression that pablo#253 (ToastProvider mount
 * missing) shipped on 2026-05-22: the chart route loads, the SPA
 * hydrates, but a global provider is gone and the page crashes on
 * render. Existing chart-touching specs only exercise the chart for
 * specific features (upload, chat) — none assert "chart mounts cleanly
 * with no console errors", so a provider regression slipped through
 * the existing suite.
 *
 * What this proves:
 *   1. A signed-in user can navigate to a patient chart.
 *   2. The chart's primary sections render (patient name h1 from
 *      PatientSummary, "Chart" h2 from PatientChartTabs, plus the
 *      Notes and Documents tab triggers) — four landmarks across the
 *      top-level chart components, so a missing provider on any one
 *      of them surfaces.
 *   3. No console.error or uncaught page exception fires during the
 *      whole visit (covers React error boundary trips, hydration
 *      mismatches, missing-context crashes).
 *
 * Out of scope: data assertions (note count, document list), switching
 * to the Documents tab, chat modal contents (covered by chat.spec.ts).
 *
 * Cost: ~3-5s after signedInPage warm-up. Cheap enough to keep in
 * the post-deploy gate.
 */
import { expect, test } from "../fixtures/auth";
import { attachConsoleGuard } from "../fixtures/consoleGuard";
import { waitForAppReady } from "../flows/onboarding";
import { scenarios } from "../fixtures/scenarios";

test("patient chart mounts cleanly with no console errors @smoke @chart", async ({
  onboardedUser,
  signedInPage,
}) => {
  const page = signedInPage;
  const guard = attachConsoleGuard(page);

  try {
    const patient = await scenarios.givePatient(onboardedUser);

    await page.goto(`/dashboard/patients/${patient.id}`);
    await waitForAppReady(page);

    // Three landmarks across three top-level chart components — a
    // provider regression localized to any one of them surfaces.
    await expect(
      page.getByRole("heading", {
        name: `${patient.first_name} ${patient.last_name}`,
      }),
      "patient header h1 renders (PatientDetailPage)",
    ).toBeVisible({ timeout: 15_000 });

    await expect(
      page.getByRole("heading", { name: /^Chart$/ }),
      "Chart section h2 renders (PatientChartTabs)",
    ).toBeVisible({ timeout: 10_000 });

    await expect(
      page.getByRole("tab", { name: /Notes/ }),
      "Notes tab trigger renders (PatientChartTabs)",
    ).toBeVisible({ timeout: 10_000 });

    await expect(
      page.getByRole("tab", { name: /Documents/ }),
      "Documents tab trigger renders (PatientChartTabs)",
    ).toBeVisible({ timeout: 10_000 });

    // No console errors / no uncaught exceptions during the whole
    // visit. This is the load-bearing assertion — landmarks above
    // could pass even when a provider is broken if the broken bit is
    // a Toaster portal that mounts elsewhere.
    expect(
      guard.pageErrors.map((e) => e.message),
      "no uncaught page exceptions",
    ).toEqual([]);
    expect(
      guard.errors,
      "no console.error messages",
    ).toEqual([]);
  } finally {
    guard.dispose();
  }
});
