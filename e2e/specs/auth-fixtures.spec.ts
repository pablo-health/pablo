/**
 * Smoke test for the unenrolledUser fixture. Asserts the post-signUp
 * pre-wizard state — Firebase user exists, idToken is a JWT, no
 * second-factor claim yet (MFA is added by the wizard, not this fixture).
 *
 * The MFA-enrolled state is covered end-to-end by chat.spec.ts and
 * patient-document-upload.spec.ts through the shared onboardedUser
 * fixture — a dedicated fixture smoke test would just duplicate that.
 */
import { expect, test } from "../fixtures/auth";

test("unenrolledUser fixture provisions and cleans up a fresh user", async ({
  unenrolledUser,
}) => {
  expect(unenrolledUser.email, "email").toMatch(/^e2etest-[0-9a-f]{8}@pablo\.health$/);
  expect(unenrolledUser.uid, "uid").toBeTruthy();
  expect(unenrolledUser.idToken, "idToken").toBeTruthy();

  const payload = JSON.parse(
    Buffer.from(unenrolledUser.idToken.split(".")[1], "base64url").toString(
      "utf8",
    ),
  );
  expect(payload.sub).toBe(unenrolledUser.uid);
  // Unenrolled user MUST NOT have the second-factor claim
  expect(payload.firebase?.sign_in_second_factor).toBeUndefined();
});
