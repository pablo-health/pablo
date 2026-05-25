/**
 * Tiered Playwright fixtures. Pick the cheapest one that gives you
 * the state your test needs.
 *
 *   unenrolledUser     PER-TEST     signUp + email-verified. For the
 *                                   onboarding wizard regression — the
 *                                   wizard does the rest in the UI.
 *
 *   onboardedUser      PER-WORKER   real UI onboarding wizard driven
 *                                   once → MFA-enrolled + BAA-accepted.
 *                                   Production-faithful state. In pinned
 *                                   mode (PINNED_EMAIL + TOTP_SECRET +
 *                                   TEST_PASSWORD) skips the wizard and
 *                                   reuses the bootstrapped user.
 *
 *   enrolledUser       PER-WORKER   alias for onboardedUser (structural
 *                                   subset). Specs that just need to be
 *                                   signed-in use this so they don't
 *                                   advertise a dependency on UI state.
 *
 *   signedInPage       PER-TEST     a Page whose context is signed in
 *                                   as onboardedUser via UI (~3-5s/test).
 *
 * Pablo runs single-tenant by default (ENABLE_MULTI_TENANCY=false), so
 * none of these fixtures provision a tenant — the auth chain (signUp →
 * verify → MFA → BAA) is all that's needed.
 *
 * Cleanup happens at the right scope automatically — worker-scoped
 * fixtures live for the worker, per-test ones for the test. State
 * pollution discipline still applies to per-worker users: use unique
 * IDs in test data, never assert exact list counts.
 */
import { test as base } from "@playwright/test";
import { randomBytes } from "node:crypto";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { GoogleAuth } from "google-auth-library";

import { requireEnv } from "./env";
import {
  deleteAccount,
  setEmailVerified,
  signInWithMfa,
  signUp,
  type SignInResult,
} from "./firebaseAuth";
import { freshTotp } from "./totp";
import { completeOnboarding } from "../flows/onboarding";

// ── shared helpers ──────────────────────────────────────────────────

let cachedAuth: GoogleAuth | undefined;
async function getAdminAccessToken(): Promise<string> {
  cachedAuth ??= new GoogleAuth({
    scopes: ["https://www.googleapis.com/auth/cloud-platform"],
  });
  const client = await cachedAuth.getClient();
  const tokenResp = await client.getAccessToken();
  const token = tokenResp.token;
  if (!token) throw new Error("Could not obtain admin access token from ADC");
  return token;
}

function requireProjectId(): string {
  return process.env.FIREBASE_PROJECT_ID ?? "pablohealth-oss";
}

let cachedApiUrl: string | undefined;
async function discoverApiUrl(): Promise<string> {
  if (cachedApiUrl) return cachedApiUrl;
  if (process.env.PABLO_API_URL) {
    cachedApiUrl = process.env.PABLO_API_URL;
    return cachedApiUrl;
  }
  const base = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000";
  const resp = await fetch(`${base}/api/config`);
  const cfg = (await resp.json()) as { apiUrl?: string };
  if (!cfg.apiUrl) throw new Error("/api/config returned no apiUrl");
  cachedApiUrl = cfg.apiUrl;
  return cachedApiUrl;
}

/**
 * Reserved e2etest-<8hex>@pablo.health prefix bypasses the sign-up
 * allowlist (pablo#221/#222) so the e2e user can self-provision against
 * a deployed environment.
 */
function generateTestEmail(): string {
  const noise = randomBytes(4).toString("hex");
  return `e2etest-${noise}@pablo.health`;
}

type PinnedConfig = {
  email: string;
  password: string;
  totpSecret: string;
};

/**
 * Pinned-mode env-var path. When PINNED_EMAIL + TOTP_SECRET (+
 * TEST_PASSWORD) are set, fixtures sign in as a long-lived bootstrapped
 * user instead of creating a fresh one. Returns undefined when neither
 * PINNED_EMAIL nor TOTP_SECRET is set; throws when only one is set so
 * half-configured runs fail loudly instead of silently falling back to
 * fresh-user mode (which can mask a config bug).
 */
function readPinnedConfig(): PinnedConfig | undefined {
  const email = process.env.PINNED_EMAIL;
  const totpSecret = process.env.TOTP_SECRET;
  const set = [email, totpSecret].filter(Boolean).length;
  if (set === 0) return undefined;
  if (set !== 2) {
    throw new Error(
      "Pinned-mode partial config: set BOTH PINNED_EMAIL and TOTP_SECRET " +
        "(plus TEST_PASSWORD) or neither.",
    );
  }
  const password = process.env.TEST_PASSWORD;
  if (!password) {
    throw new Error("Pinned mode requires TEST_PASSWORD (the pinned user's password)");
  }
  return { email: email!, password, totpSecret: totpSecret! };
}

// ── public fixture types ────────────────────────────────────────────

export type UnenrolledUser = {
  email: string;
  password: string;
  uid: string;
  idToken: string;
};

export type EnrolledUser = {
  email: string;
  password: string;
  uid: string;
  totpSecret: string;
  apiUrl: string;
  /**
   * Mint a fresh idToken via REST re-sign-in. Always prefer this over
   * caching the initial token — workers can outlive the ~1h token TTL.
   */
  getIdToken: () => Promise<string>;
};

export type OnboardedUser = EnrolledUser & {
  /** Path to a Playwright storageState JSON captured after UI onboarding. */
  storageStatePath: string;
};

import type { BrowserContext, Page } from "@playwright/test";

type WorkerFixtures = {
  enrolledUser: EnrolledUser;
  onboardedUser: OnboardedUser;
  signedInContext: BrowserContext;
};

type Fixtures = {
  unenrolledUser: UnenrolledUser;
  signedInPage: Page;
};

// ── implementation ──────────────────────────────────────────────────

export const test = base.extend<Fixtures, WorkerFixtures>({
  // PER-WORKER: thin alias for onboardedUser. EnrolledUser is a
  // structural subset of OnboardedUser, so chaining costs nothing and
  // collapses the two prior worker users into one (1 MFA enrollment
  // per worker instead of 2). Specs that don't need UI state should
  // still depend on enrolledUser to keep the intent clear.
  enrolledUser: [
    async ({ onboardedUser }, use) => {
      await use(onboardedUser);
    },
    { scope: "worker" },
  ],

  // PER-WORKER: the canonical authenticated test user.
  //
  // Pinned mode (PINNED_EMAIL + TOTP_SECRET + TEST_PASSWORD): skip the
  // wizard walk and reuse the bootstrapped pinned user. Cleanup is a
  // no-op. Bootstrap via scripts/provision-pinned-user.ts.
  //
  // Fresh mode: signUp + verify + drive the real UI wizard once
  // (MFA + BAA + welcome). Wizard adds ~20s at worker init; every
  // subsequent test pays only sign-in cost. Fresh mode needs an admin
  // token (setEmailVerified) — see e2e/README.md.
  onboardedUser: [
    async ({ browser }, use) => {
      const apiKey = requireEnv("FIREBASE_API_KEY");
      const apiUrl = await discoverApiUrl();
      const pinned = readPinnedConfig();

      if (pinned) {
        // Cache the idToken with a soft TTL so getIdToken() doesn't
        // re-sign-in on every call. Each REST signInWithMfa burns a TOTP
        // window AND a few IDP REST calls — back-to-back calls can trip
        // Firebase's per-IP quota and its replayed-code rejection.
        let cachedToken: string | undefined;
        let cachedAt = 0;
        const TOKEN_TTL_MS = 45 * 60 * 1000;
        const mintIdToken = async (): Promise<string> => {
          if (cachedToken && Date.now() - cachedAt < TOKEN_TTL_MS) {
            return cachedToken;
          }
          const fresh = await signInWithMfa({
            apiKey,
            email: pinned.email,
            password: pinned.password,
            totpSecret: pinned.totpSecret,
          });
          cachedToken = fresh.idToken;
          cachedAt = Date.now();
          return cachedToken;
        };
        // Prime the cache so the first test gets a ready token.
        await mintIdToken();

        const onboarded: OnboardedUser = {
          email: pinned.email,
          password: pinned.password,
          uid: "pinned",
          totpSecret: pinned.totpSecret,
          apiUrl,
          // signedInPage signs in via UI per test, so the storageState
          // file is never read in practice. Empty path is fine.
          storageStatePath: "",
          getIdToken: mintIdToken,
        };
        console.error(`[onboardedUser] pinned mode: ${pinned.email}`);
        await use(onboarded);
        return; // no cleanup — pinned user persists
      }

      const password = requireEnv("TEST_PASSWORD");
      const projectId = requireProjectId();
      const email = generateTestEmail();
      const adminAccessToken = await getAdminAccessToken();

      let signed: SignInResult | undefined;
      let totpSecret: string | undefined;
      let storageStatePath: string | undefined;
      try {
        // signUp + verify. The wizard does MFA + BAA + the rest.
        signed = await signUp({ apiKey, email, password });
        await setEmailVerified({
          projectId,
          accessToken: adminAccessToken,
          uid: signed.uid,
        });

        // Drive the full UI wizard once. Capture TOTP secret so we can
        // re-sign-in for cleanup + for any spec that needs a fresh
        // idToken via getIdToken().
        const ctx = await browser.newContext();
        const page = await ctx.newPage();
        const result = await completeOnboarding(page, { email, password });
        totpSecret = result.totpSecret;

        // Snapshot signed-in state before closing the temp context.
        const tmp = await mkdtemp(join(tmpdir(), "e2e-storage-"));
        storageStatePath = join(tmp, "state.json");
        await ctx.storageState({ path: storageStatePath });
        await ctx.close();

        // Cache the most recent idToken + a soft TTL so getIdToken() doesn't
        // mint a fresh one per call. Each REST signInWithMfa burns a TOTP
        // window — back-to-back calls within ~30s trigger Firebase's
        // INVALID_CODE rejection because it dedupes recently-consumed codes.
        // The initial signInWithMfa here gives us a token usable for ~1h.
        let cachedToken: string | undefined;
        let cachedAt = 0;
        const TOKEN_TTL_MS = 45 * 60 * 1000; // refresh 15min before Firebase's ~1h expiry
        const mintIdToken = async (): Promise<string> => {
          if (cachedToken && Date.now() - cachedAt < TOKEN_TTL_MS) {
            return cachedToken;
          }
          const fresh = await signInWithMfa({
            apiKey,
            email,
            password,
            totpSecret: totpSecret!,
          });
          cachedToken = fresh.idToken;
          cachedAt = Date.now();
          return cachedToken;
        };
        // Prime the cache so the worker hands the first test a token
        // that's already valid + doesn't burn a fresh TOTP code.
        await mintIdToken();

        const onboarded: OnboardedUser = {
          email,
          password,
          uid: signed.uid,
          totpSecret,
          apiUrl,
          storageStatePath,
          getIdToken: mintIdToken,
        };
        await use(onboarded);
      } finally {
        if (signed && totpSecret) {
          try {
            const fresh = await signInWithMfa({
              apiKey,
              email,
              password,
              totpSecret,
            });
            await deleteAccount({ apiKey, idToken: fresh.idToken });
          } catch (err) {
            console.error(`[onboardedUser] user cleanup failed for ${email}:`, err);
          }
        } else if (signed) {
          // Onboarding never finished — original signUp idToken should
          // still work for delete.
          await deleteAccount({ apiKey, idToken: signed.idToken }).catch(
            (err) => {
              console.error(
                `[onboardedUser] partial cleanup failed for ${email}:`,
                err,
              );
            },
          );
        }
      }
    },
    { scope: "worker" },
  ],

  // PER-WORKER: a BrowserContext that has driven the UI sign-in flow
  // exactly once. Firebase Auth's web SDK persists the session in
  // IndexedDB at the BrowserContext level, so every page opened in this
  // context loads already-signed-in without re-running MFA.
  //
  // Why this isn't per-test: Firebase Identity Platform throttles
  // mfaSignIn:finalize at the project level (~100/min). With N specs
  // using signedInPage × M workers × multiple deploys per hour, a
  // per-test sign-in burns the quota and the gate goes red on every
  // run. Sharing the signed-in context drops the call count to 1 per
  // worker.
  signedInContext: [
    async ({ browser, onboardedUser }, use) => {
      const ctx = await browser.newContext();
      const page = await ctx.newPage();
      try {
        await signInWithMfaViaUi(page, onboardedUser);
      } finally {
        await page.close();
      }
      await use(ctx);
      await ctx.close();
    },
    { scope: "worker" },
  ],

  // PER-TEST: a fresh Page on the already-signed-in worker context.
  // Each test gets its own page (so concurrent tests in a worker don't
  // race on a shared page) but shares the IndexedDB auth state with
  // all other tests in the same worker.
  signedInPage: async ({ signedInContext }, use) => {
    const page = await signedInContext.newPage();
    try {
      await use(page);
    } finally {
      await page.close();
    }
  },

  // PER-TEST: signUp + email-verified only. The UI wizard does the rest.
  // The onboarding spec uses this for its dedicated wizard regression
  // coverage. Needs an admin token (setEmailVerified) — see README.
  unenrolledUser: async ({}, use) => {
    const apiKey = requireEnv("FIREBASE_API_KEY");
    const password = requireEnv("TEST_PASSWORD");
    const projectId = requireProjectId();
    const email = generateTestEmail();
    const adminAccessToken = await getAdminAccessToken();

    let user: UnenrolledUser | undefined;
    try {
      const signed = await signUp({ apiKey, email, password });
      // Firebase rejects MFA enrollment for unverified emails (both
      // REST and SPA's web SDK). Mark verified so the wizard can run.
      await setEmailVerified({
        projectId,
        accessToken: adminAccessToken,
        uid: signed.uid,
      });
      user = {
        email,
        password,
        uid: signed.uid,
        idToken: signed.idToken,
      };
      await use(user);
    } finally {
      if (user) {
        await deleteAccount({ apiKey, idToken: user.idToken }).catch((err) => {
          console.error(`[unenrolledUser] user cleanup failed for ${email}:`, err);
        });
      }
    }
  },
});

/**
 * Sign in via /login form for a user that's already MFA-enrolled.
 * Two-step: password → TOTP challenge. Lands on /dashboard.
 *
 * Used by `signedInPage` per-test because Playwright storageState
 * can't carry Firebase's IndexedDB auth state.
 */
async function signInWithMfaViaUi(
  page: Page,
  user: { email: string; password: string; totpSecret: string },
): Promise<void> {
  await page.goto("/login");
  await page.getByText("Loading configuration").waitFor({
    state: "hidden",
    timeout: 15_000,
  });
  await page.getByLabel("Email").fill(user.email);
  await page.getByLabel("Password", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: /^Sign In$/i }).click();
  // MFA challenge step — MfaChallengeScreen uses #totp-code (the
  // enrollment form uses #verification-code; sign-in challenge is
  // a different component).
  const codeInput = page.locator("#totp-code");
  await codeInput.waitFor({ state: "visible", timeout: 15_000 });
  const code = await freshTotp(user.totpSecret);
  await codeInput.fill(code);
  await page.getByRole("button", { name: /verify|continue|submit/i }).first().click();
  await page.waitForURL(/\/dashboard/, { timeout: 30_000 });
}

export { expect } from "@playwright/test";
