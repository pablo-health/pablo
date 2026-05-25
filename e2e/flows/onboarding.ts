/**
 * Onboarding page-object.
 *
 * Drives a freshly REST-signed-up user through the full onboarding
 * wizard:
 *
 *   1. /login                — sign in (email + password)
 *   2. /onboarding/welcome   — "Get started"
 *   3. /onboarding/provider-type — pick role
 *   4. /onboarding/security-guide — acknowledge
 *   5. /onboarding/baa       — fill professional info + accept (handle nudge)
 *   6. /onboarding/mfa       — scrape TOTP secret from "manual entry" panel,
 *                              generate code, enroll
 *   7. /onboarding/celebration — "Go to dashboard"
 *   8. /dashboard            — verify landing
 *
 * Returns the TOTP secret captured during enrollment so subsequent
 * runs in the same test can re-sign-in if needed.
 *
 * Why no UI signup: pablo's /login page does both sign-in and sign-up
 * via a toggle, but the signUp branch triggers Firebase
 * sendEmailVerification — verification requires a real mailbox, which
 * we don't have. Our `unenrolledUser` fixture creates the user via REST
 * instead, then this flow signs them in via the UI.
 */
import { expect, type Page } from "@playwright/test";

import { freshTotp } from "../fixtures/totp";

export type OnboardingResult = {
  totpSecret: string;
};

export type OnboardingProfile = {
  legalName: string;
  licenseNumber: string;
  licenseState: string; // 2-letter code
  businessAddress: string;
  providerType: "therapist" | "prescriber" | "both";
};

const DEFAULT_PROFILE: OnboardingProfile = {
  legalName: "Test User",
  licenseNumber: "E2E-TEST-12345",
  licenseState: "CA",
  businessAddress: "123 Test Street\nSan Francisco, CA 94101",
  providerType: "therapist",
};

/**
 * Pablo's SPA shows a "Loading configuration..." splash while fetching
 * /api/config. page.goto() returns on first paint — well before the
 * SPA hydrates — so every spec that touches the app needs to wait
 * past this splash before asserting (~600ms locally; allow 15s for
 * slow CI).
 */
export async function waitForAppReady(page: Page): Promise<void> {
  await expect(page.getByText("Loading configuration")).toBeHidden({
    timeout: 15_000,
  });
}

/**
 * Sign in via the /login form. Used after REST signUp to put the
 * Firebase web SDK into a signed-in state inside the browser context.
 */
export async function signInWithEmailPassword(
  page: Page,
  args: { email: string; password: string },
): Promise<void> {
  await page.goto("/login");
  await waitForAppReady(page);

  await page.getByLabel("Email").fill(args.email);
  // The login page reuses #password for both sign-in and sign-up modes;
  // we want sign-in so label is just "Password".
  await page.getByLabel("Password", { exact: true }).fill(args.password);
  await page.getByRole("button", { name: /^Sign In$/i }).click();

  // After login, the SPA navigates to /dashboard or /onboarding/* depending
  // on user state. We don't assert the destination here — each step helper
  // navigates to its own URL.
  await page.waitForURL(/\/(dashboard|onboarding)/, { timeout: 30_000 });
}

export async function completeWelcomeStep(page: Page): Promise<void> {
  await page.goto("/onboarding/welcome");
  await waitForAppReady(page);
  await page.getByRole("button", { name: /Get started/i }).click();
  await page.waitForURL(/\/onboarding(?!\/welcome)/, { timeout: 15_000 });
}

export async function completeProviderTypeStep(
  page: Page,
  choice: OnboardingProfile["providerType"] = "therapist",
): Promise<void> {
  await page.goto("/onboarding/provider-type");
  await waitForAppReady(page);
  await page.locator(`input[name="provider-type"][value="${choice}"]`).check();
  await page.getByRole("button", { name: /^Continue$/i }).click();
  await page.waitForURL(/\/onboarding(?!\/provider-type)/, { timeout: 15_000 });
}

export async function completeSecurityGuideStep(page: Page): Promise<void> {
  await page.goto("/onboarding/security-guide");
  await waitForAppReady(page);
  await page
    .getByLabel(/I.{1,3}ve read the security.+privacy guide/i)
    .check();
  await page.getByRole("button", { name: /^Continue$/i }).click();
  await page.waitForURL(/\/onboarding(?!\/security-guide)/, {
    timeout: 15_000,
  });
}

export async function completeBaaStep(
  page: Page,
  profile: Pick<
    OnboardingProfile,
    "legalName" | "licenseNumber" | "licenseState" | "businessAddress"
  > = DEFAULT_PROFILE,
): Promise<void> {
  await page.goto("/onboarding/baa");
  await waitForAppReady(page);

  await page.locator("#legalName").fill(profile.legalName);
  await page.locator("#licenseNumber").fill(profile.licenseNumber);
  await page.locator("#licenseState").selectOption(profile.licenseState);
  await page.locator("#businessAddress").fill(profile.businessAddress);
  await page
    .getByLabel(/I have read and agree to the Business Associate Agreement/i)
    .check();

  await page.getByRole("button", { name: /Accept and Continue/i }).click();

  // The "scroll first" nudge dialog (post-#203) fires when the user
  // hasn't scrolled to the bottom of the BAA text. Dismiss it by
  // accepting through the dialog button if it appears.
  const nudgeAccept = page.getByRole("button", { name: /I.{1,3}ve got it, accept/i });
  if (await nudgeAccept.isVisible({ timeout: 2_000 }).catch(() => false)) {
    await nudgeAccept.click();
  }

  await page.waitForURL(/\/onboarding(?!\/baa)/, { timeout: 30_000 });
}

/**
 * Scrape the TOTP secret from the MFA enrollment form's "manual entry"
 * panel, generate a fresh code, submit it.
 *
 * The form (pablo/frontend/app/mfa-enrollment) renders a QR code by
 * default and exposes the raw secret behind a "Show manual entry key"
 * toggle. We click that toggle, read the <code> element holding
 * `totpSecret.secretKey`, and use otplib (via fixtures/totp.ts) to mint
 * the verification code.
 *
 * Returns the secret so the caller can re-use it for later sign-ins
 * within the same test.
 */
export async function completeMfaStep(page: Page): Promise<OnboardingResult> {
  await page.goto("/onboarding/mfa");
  await waitForAppReady(page);

  // The form shows a "Generating secure key..." spinner while it calls
  // Firebase mfaEnrollment:start. Wait for the manual-entry toggle to
  // appear, which means the secret is available.
  const showManual = page.getByRole("button", {
    name: /Show manual entry key/i,
  });
  await showManual.waitFor({ state: "visible", timeout: 30_000 });
  await showManual.click();

  // The secret is inside a <code> element in the panel that just opened.
  const secretCode = page.locator("code").first();
  await secretCode.waitFor({ state: "visible", timeout: 5_000 });
  const totpSecret = (await secretCode.textContent())?.trim();
  if (!totpSecret) {
    throw new Error("Could not read TOTP secret from manual-entry panel");
  }

  // Generate a code on a fresh window so it doesn't expire mid-submit.
  const code = await freshTotp(totpSecret);
  await page.locator("#verification-code").fill(code);
  await page.getByRole("button", { name: /Enable MFA/i }).click();

  // After enroll, the form redirects via router.push(returnTo) → /onboarding,
  // which then dispatches to /onboarding/celebration.
  await page.waitForURL(/\/onboarding(?!\/mfa)/, { timeout: 30_000 });
  return { totpSecret };
}

export async function completeCelebrationStep(page: Page): Promise<void> {
  await page.goto("/onboarding/celebration");
  await waitForAppReady(page);
  await page.getByRole("button", { name: /Go to dashboard/i }).click();
  await page.waitForURL(/\/dashboard(?!\/onboarding)/, { timeout: 30_000 });
}

/**
 * Run the full onboarding wizard end to end. Caller provides a
 * REST-signed-up user (from the `unenrolledUser` fixture) — this helper
 * drives the UI from sign-in through to the dashboard.
 */
export async function completeOnboarding(
  page: Page,
  user: { email: string; password: string },
  profile: OnboardingProfile = DEFAULT_PROFILE,
): Promise<OnboardingResult> {
  await signInWithEmailPassword(page, user);
  await completeWelcomeStep(page);
  await completeProviderTypeStep(page, profile.providerType);
  await completeSecurityGuideStep(page);
  await completeBaaStep(page, profile);
  const mfa = await completeMfaStep(page);
  await completeCelebrationStep(page);

  // Final assertion: dashboard renders. Specific selector kept loose
  // since dashboard chrome may evolve; we just confirm we're past the
  // wizard and on a non-onboarding route.
  await expect(page).toHaveURL(/\/dashboard/);
  return mfa;
}
