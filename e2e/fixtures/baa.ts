/**
 * Accept the Business Associate Agreement for a test user.
 *
 * Why this exists: the `testUser` / `sharedUser` fixtures provision a
 * tenant and enroll MFA, but do not flip `baa_accepted_at` on the user
 * row. Any endpoint gated by `require_baa_acceptance` (pablo
 * backend/app/auth/service.py:637) — chat, sessions, patients,
 * patient_documents — returns 403 until BAA is accepted.
 *
 * Endpoints:
 *   GET  /api/users/me/baa-status   → { current_version: "YYYY-MM-DD", ... }
 *   POST /api/users/me/accept-baa   → records acceptance
 *
 * Auth: pre-MFA token works (`get_current_user_no_mfa`), but in our
 * fixtures we always have a post-MFA idToken on hand, which is also
 * accepted.
 */

const DUMMY_BAA_FIELDS = {
  legal_name: "E2E Test User",
  license_number: "E2E-TEST-0000",
  license_state: "CA",
  business_address: "123 Test Street, San Francisco, CA 94110",
  practice_name: "E2E Test Practice",
} as const;

async function getCurrentBaaVersion(args: {
  apiUrl: string;
  idToken: string;
}): Promise<string> {
  const resp = await fetch(`${args.apiUrl}/api/users/me/baa-status`, {
    headers: { Authorization: `Bearer ${args.idToken}` },
  });
  if (!resp.ok) {
    throw new Error(
      `getCurrentBaaVersion failed: ${resp.status} ${await resp.text()}`,
    );
  }
  const body = (await resp.json()) as { current_version?: string };
  if (!body.current_version) {
    throw new Error("baa-status response missing current_version");
  }
  return body.current_version;
}

export async function acceptBaaForUser(args: {
  apiUrl: string;
  idToken: string;
}): Promise<void> {
  const version = await getCurrentBaaVersion(args);
  const resp = await fetch(`${args.apiUrl}/api/users/me/accept-baa`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${args.idToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ...DUMMY_BAA_FIELDS, version, accepted: true }),
  });
  if (!resp.ok) {
    throw new Error(
      `acceptBaaForUser failed: ${resp.status} ${await resp.text()}`,
    );
  }
}
