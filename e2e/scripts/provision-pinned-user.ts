#!/usr/bin/env tsx
/**
 * Provision the pinned e2e test user against a deployed pablo
 * environment (default: pablohealth-oss).
 *
 * Pablo runs single-tenant by default (ENABLE_MULTI_TENANCY=false), so
 * this script's job is the auth chain only: signUp → verify email →
 * enroll MFA → accept BAA. No tenant bootstrap needed.
 *
 *   TEST_PASSWORD=...  OUT_PATH=/tmp/pinned.json  \
 *     ./node_modules/.bin/tsx scripts/provision-pinned-user.ts
 *
 * Env vars:
 *   TEST_PASSWORD        required — strong password to enroll the user with
 *   OUT_PATH             required — file to write the credentials JSON to.
 *                        Created with mode 0600. Written to a file rather
 *                        than stdout so process snapshots and CI logs never
 *                        see the password or TOTP secret in cleartext.
 *   PINNED_EMAIL         default: e2etest-deadbeef@pablo.health (must match
 *                        OSS E2E_EMAIL_PATTERN: ^e2etest-[0-9a-f]{8}@pablo\.health$)
 *   PLAYWRIGHT_BASE_URL  default: discovered via gcloud against pablo-frontend
 *                        on pablohealth-oss
 *   FIREBASE_API_KEY     default: pulled from /api/config
 *   FIREBASE_PROJECT_ID  default: pablohealth-oss
 *
 * Output: writes {email, password, totpSecret, uid} JSON to OUT_PATH
 * (mode 0600). Caller stashes the values in pablohealth-oss Secret
 * Manager — see e2e/README.md "Bootstrap" for the one-time gcloud
 * commands.
 *
 * Idempotency: not implemented. If the email already exists in Firebase
 * the script fails with EMAIL_EXISTS — delete via the Firebase Console
 * (Identity Platform → Users) and retry.
 */
import { writeFileSync } from "node:fs";

import { GoogleAuth } from "google-auth-library";

import { acceptBaaForUser } from "../fixtures/baa";
import { provisionMfaUser } from "../fixtures/firebaseAuth";

const PINNED_EMAIL =
  process.env.PINNED_EMAIL ?? "e2etest-deadbeef@pablo.health";

async function discoverApiUrl(): Promise<string> {
  if (process.env.PABLO_API_URL) return process.env.PABLO_API_URL;
  const base = process.env.PLAYWRIGHT_BASE_URL;
  if (!base) {
    throw new Error(
      "PLAYWRIGHT_BASE_URL env required (e.g. pablo-frontend's Cloud Run URL)",
    );
  }
  const resp = await fetch(`${base}/api/config`);
  const cfg = (await resp.json()) as { apiUrl?: string };
  if (!cfg.apiUrl) throw new Error("/api/config returned no apiUrl");
  return cfg.apiUrl;
}

async function discoverFirebaseApiKey(): Promise<string> {
  if (process.env.FIREBASE_API_KEY) return process.env.FIREBASE_API_KEY;
  const base = process.env.PLAYWRIGHT_BASE_URL;
  if (!base) throw new Error("PLAYWRIGHT_BASE_URL env required");
  const resp = await fetch(`${base}/api/config`);
  const cfg = (await resp.json()) as { firebaseApiKey?: string };
  if (!cfg.firebaseApiKey)
    throw new Error("/api/config returned no firebaseApiKey");
  return cfg.firebaseApiKey;
}

async function getAdminAccessToken(): Promise<string> {
  const auth = new GoogleAuth({
    scopes: ["https://www.googleapis.com/auth/cloud-platform"],
  });
  const client = await auth.getClient();
  const tok = await client.getAccessToken();
  if (!tok.token) throw new Error("Could not mint admin access token via ADC");
  return tok.token;
}

async function main() {
  if (!/^e2etest-[0-9a-f]{8}@pablo\.health$/.test(PINNED_EMAIL)) {
    throw new Error(
      `PINNED_EMAIL ${PINNED_EMAIL} does not match the OSS ` +
        "E2E_EMAIL_PATTERN (^e2etest-[0-9a-f]{8}@pablo\\.health$). The " +
        "backend's check_allowlist + auth-test paths rely on the 8-hex tail.",
    );
  }
  const password = process.env.TEST_PASSWORD;
  if (!password)
    throw new Error("TEST_PASSWORD env required (strong shared password)");
  const outPath = process.env.OUT_PATH;
  if (!outPath)
    throw new Error(
      "OUT_PATH env required — credentials are written to a 0600 file, not stdout.",
    );

  const projectId = process.env.FIREBASE_PROJECT_ID ?? "pablohealth-oss";
  const apiUrl = await discoverApiUrl();
  const apiKey = await discoverFirebaseApiKey();
  const adminAccessToken = await getAdminAccessToken();

  console.error(`[provision] email=${PINNED_EMAIL} apiUrl=${apiUrl}`);

  console.error("[provision] step 1/2: signUp + verify + MFA enroll…");
  const provisioned = await provisionMfaUser({
    apiKey,
    projectId,
    adminAccessToken,
    email: PINNED_EMAIL,
    password,
  });

  console.error("[provision] step 2/2: accept BAA…");
  await acceptBaaForUser({ apiUrl, idToken: provisioned.idToken });

  const out = {
    email: PINNED_EMAIL,
    password,
    totpSecret: provisioned.totpSecret,
    uid: provisioned.uid,
  };
  // Never log credentials (password, TOTP secret) to stdout/stderr —
  // write them to a 0600 file the operator passes to Secret Manager.
  writeFileSync(outPath, JSON.stringify(out, null, 2), { mode: 0o600 });
  console.error(`[provision] done. credentials written to ${outPath} (mode 0600)`);
}

main().catch((err) => {
  console.error("[provision] FAILED:", err);
  process.exit(1);
});
