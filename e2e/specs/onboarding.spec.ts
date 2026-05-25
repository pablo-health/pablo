/**
 * End-to-end verification of the onboarding flow page-object.
 *
 * Uses the unenrolledUser fixture: REST signUp creates the (email-
 * verified) user, but MFA enrollment happens through the actual UI in
 * this spec. That's the point — exercising the wizard chain end to end
 * is what we'd lose coverage on if we shortcut to programmatic MFA via
 * REST.
 */
import { test, expect } from "../fixtures/auth";
import { completeOnboarding } from "../flows/onboarding";

test("drives a fresh user through the full onboarding wizard to dashboard", async ({
  page,
  unenrolledUser,
}) => {
  const result = await completeOnboarding(page, {
    email: unenrolledUser.email,
    password: unenrolledUser.password,
  });

  expect(result.totpSecret, "captured TOTP secret").toBeTruthy();
  expect(result.totpSecret).toMatch(/^[A-Z2-7]+$/); // base32 alphabet
  await expect(page).toHaveURL(/\/dashboard/);
});
