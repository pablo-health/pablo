/**
 * Firebase Identity Platform REST client, modeled on the auth flow in
 * .claude/skills/pentest/SKILL.md.
 *
 * Endpoints:
 *   v1/accounts:signUp                  → create user (triggers blocking fn → tenant provisioning)
 *   v1/accounts:signInWithPassword      → password sign-in; returns mfaPendingCredential if MFA enrolled
 *   v2/accounts/mfaEnrollment:start     → TOTP enrollment kickoff; returns sharedSecretKey
 *   v2/accounts/mfaEnrollment:finalize  → confirm enrollment with a TOTP code
 *   v2/accounts/mfaSignIn:finalize      → finish MFA sign-in (no `start` needed for TOTP)
 *   v1/accounts:delete                  → tear down test user (cleanup)
 *
 * All calls go directly to identitytoolkit.googleapis.com. The Firebase
 * API key gates these endpoints; the calling SA does NOT need any
 * Firebase IAM role.
 */
import { freshTotp, generateTotp } from "./totp";

const IDP_BASE = "https://identitytoolkit.googleapis.com";

export type SignUpResult = {
  uid: string;
  idToken: string;
  refreshToken: string;
  expiresIn: string;
};

export type MfaEnrollmentResult = {
  /** Base32 shared secret — pass to generateTotp() / freshTotp() */
  sharedSecretKey: string;
  /** Returned new tokens reflecting MFA enrollment */
  idToken: string;
  refreshToken: string;
};

export type SignInResult = {
  idToken: string;
  refreshToken: string;
  uid: string;
};

async function idpFetch<T>(
  path: string,
  apiKey: string,
  body: unknown,
): Promise<T> {
  const url = `${IDP_BASE}/${path}?key=${encodeURIComponent(apiKey)}`;
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await resp.text();
  if (!resp.ok) {
    throw new Error(`IDP ${path} failed: ${resp.status} ${text}`);
  }
  return JSON.parse(text) as T;
}

export async function signUp(args: {
  apiKey: string;
  email: string;
  password: string;
}): Promise<SignUpResult> {
  const data = await idpFetch<{
    localId: string;
    idToken: string;
    refreshToken: string;
    expiresIn: string;
  }>("v1/accounts:signUp", args.apiKey, {
    email: args.email,
    password: args.password,
    returnSecureToken: true,
  });
  return {
    uid: data.localId,
    idToken: data.idToken,
    refreshToken: data.refreshToken,
    expiresIn: data.expiresIn,
  };
}

/**
 * Enroll TOTP MFA for an already-signed-in user. Returns the shared secret
 * AND new tokens that carry the `firebase.sign_in_second_factor=totp` claim.
 */
export async function enrollTotpMfa(args: {
  apiKey: string;
  idToken: string;
  displayName?: string;
}): Promise<MfaEnrollmentResult> {
  const start = await idpFetch<{
    totpSessionInfo: { sharedSecretKey: string; sessionInfo: string };
  }>("v2/accounts/mfaEnrollment:start", args.apiKey, {
    idToken: args.idToken,
    totpEnrollmentInfo: {},
  });
  const sharedSecretKey = start.totpSessionInfo.sharedSecretKey;
  const sessionInfo = start.totpSessionInfo.sessionInfo;

  // Wait for a fresh TOTP window so the code we send isn't milliseconds from expiry
  const code = await freshTotp(sharedSecretKey);

  const finalize = await idpFetch<{ idToken: string; refreshToken: string }>(
    "v2/accounts/mfaEnrollment:finalize",
    args.apiKey,
    {
      idToken: args.idToken,
      displayName: args.displayName ?? "e2e-totp",
      totpVerificationInfo: {
        sessionInfo,
        verificationCode: code,
      },
    },
  );

  return {
    sharedSecretKey,
    idToken: finalize.idToken,
    refreshToken: finalize.refreshToken,
  };
}

/**
 * Two-step MFA sign-in: password → finalize with TOTP code.
 * The TOTP finalize MUST include both mfaPendingCredential AND mfaEnrollmentId
 * (pentest skill line 68: omitting mfaEnrollmentId → confusing INVALID_ARGUMENT).
 */
export async function signInWithMfa(args: {
  apiKey: string;
  email: string;
  password: string;
  totpSecret: string;
}): Promise<SignInResult> {
  const pwResp = await idpFetch<{
    mfaPendingCredential?: string;
    mfaInfo?: Array<{ mfaEnrollmentId: string }>;
    idToken?: string;
    refreshToken?: string;
    localId?: string;
  }>("v1/accounts:signInWithPassword", args.apiKey, {
    email: args.email,
    password: args.password,
    returnSecureToken: true,
  });

  if (!pwResp.mfaPendingCredential) {
    if (pwResp.idToken && pwResp.refreshToken && pwResp.localId) {
      // No MFA enrolled — caller expected MFA. Most likely cause: enrollment failed silently.
      throw new Error(
        "signInWithMfa: account is not MFA-enrolled (no mfaPendingCredential returned)",
      );
    }
    throw new Error("signInWithMfa: unexpected response shape from signInWithPassword");
  }
  const enrollmentId = pwResp.mfaInfo?.[0]?.mfaEnrollmentId;
  if (!enrollmentId) {
    throw new Error("signInWithMfa: no mfaInfo[0].mfaEnrollmentId in response");
  }

  const code = await freshTotp(args.totpSecret);

  const final = await idpFetch<{
    idToken: string;
    refreshToken: string;
    localId?: string;
  }>("v2/accounts/mfaSignIn:finalize", args.apiKey, {
    mfaPendingCredential: pwResp.mfaPendingCredential,
    mfaEnrollmentId: enrollmentId,
    totpVerificationInfo: { verificationCode: code },
  });

  return {
    idToken: final.idToken,
    refreshToken: final.refreshToken,
    uid: final.localId ?? "",
  };
}

/**
 * Used during cleanup. Requires a *current* idToken — if the original token
 * has expired (1h), re-sign-in first to get a fresh one.
 */
export async function deleteAccount(args: {
  apiKey: string;
  idToken: string;
}): Promise<void> {
  await idpFetch<unknown>("v1/accounts:delete", args.apiKey, {
    idToken: args.idToken,
  });
}

/**
 * Mark a Firebase user's email as verified via the project-scoped
 * Identity Toolkit admin endpoint. Required before MFA enrollment —
 * Firebase rejects enrollMfa with UNVERIFIED_EMAIL otherwise, both
 * via REST and via the web SDK in the SPA's MFA wizard.
 *
 * Requires an OAuth2 access token from a service account with
 * `roles/firebaseauth.admin` on the target project (matches the
 * existing `pentest-identity` SA pattern). For local runs, ADC with
 * an owner/editor account works; for the Cloud Run Job (T5), the
 * `e2e-runner` SA must be granted firebaseauth.admin on
 * pablohealth-dev.
 */
export async function setEmailVerified(args: {
  projectId: string;
  accessToken: string;
  uid: string;
}): Promise<void> {
  const url = `https://identitytoolkit.googleapis.com/v1/projects/${encodeURIComponent(args.projectId)}/accounts:update`;
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${args.accessToken}`,
      "Content-Type": "application/json",
      "X-Goog-User-Project": args.projectId,
    },
    body: JSON.stringify({ localId: args.uid, emailVerified: true }),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`setEmailVerified failed: ${resp.status} ${text}`);
  }
}

/**
 * Convenience: signUp + immediately enroll TOTP + re-sign-in with MFA.
 * Returns a fully-provisioned, MFA-stamped session.
 *
 * Marks the user's email verified between signUp and enrollMfa —
 * Firebase blocks MFA enrollment on unverified emails. See
 * setEmailVerified() for the admin-token requirement.
 */
export async function provisionMfaUser(args: {
  apiKey: string;
  projectId: string;
  adminAccessToken: string;
  email: string;
  password: string;
}): Promise<SignInResult & { totpSecret: string }> {
  const signed = await signUp(args);
  await setEmailVerified({
    projectId: args.projectId,
    accessToken: args.adminAccessToken,
    uid: signed.uid,
  });
  // Force a token refresh so the new email_verified claim is picked up.
  // signInWithPassword returns a fresh token; we use that for enrollment.
  const refreshed = await idpFetch<{ idToken: string }>(
    "v1/accounts:signInWithPassword",
    args.apiKey,
    {
      email: args.email,
      password: args.password,
      returnSecureToken: true,
    },
  );
  const enrolled = await enrollTotpMfa({
    apiKey: args.apiKey,
    idToken: refreshed.idToken,
  });
  const mfa = await signInWithMfa({
    apiKey: args.apiKey,
    email: args.email,
    password: args.password,
    totpSecret: enrolled.sharedSecretKey,
  });
  return {
    ...mfa,
    uid: mfa.uid || signed.uid,
    totpSecret: enrolled.sharedSecretKey,
  };
}

// Re-export TOTP helpers for callers that already imported from this module.
export { freshTotp, generateTotp };
