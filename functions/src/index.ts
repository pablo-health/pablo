import { beforeUserCreated, beforeUserSignedIn } from "firebase-functions/v2/identity";
import { HttpsError } from "firebase-functions/v2/identity";
import { setGlobalOptions } from "firebase-functions/v2/options";
import { GoogleAuth } from "google-auth-library";

// Direct VPC Egress: route the blocking functions' outbound call to
// pablo-backend through the VPC so it lands as internal traffic and
// satisfies pablo-backend's `internal-and-cloud-load-balancing` ingress
// (set by THERAPY-3261). ALL_TRAFFIC is required because pablo-backend
// is reached via its public *.run.app hostname; PRIVATE_RANGES_ONLY
// would not route it through the VPC. No per-connector cost (Cloud Run
// gen2 native egress). See bead THERAPY-ilfe.
setGlobalOptions({
  networkInterface: {
    network: "default",
    subnetwork: "default",
  },
  vpcEgress: "ALL_TRAFFIC",
  // Identity Platform gives blocking functions a hard 7-second response
  // deadline, and a cold start can eat the whole budget — a user signing
  // in (or signing up) after an idle period then gets
  // BLOCKING_FUNCTION_ERROR_RESPONSE instead of a session. Keep one
  // instance warm; at 1 vCPU / 256Mi the idle cost is roughly $8 a
  // month per function.
  // Without this in code, a min-instances setting applied with gcloud is
  // silently dropped on the next `firebase deploy`.
  minInstances: 1,
});

/**
 * How long to wait on the backend before giving up on it.
 *
 * Identity Platform's blocking-function budget is a hard 7 seconds, enforced
 * from OUTSIDE the function: when it runs out the invocation is killed, so a
 * still-pending await never reaches its own catch. Both handlers below have a
 * catch that decides what an unreachable backend means — fail open for
 * sign-in, fail closed for create — and without a bound shorter than that
 * budget, NEITHER of them runs. The deadline decides instead, and it decides
 * "fail" for both, which is the opposite of what beforeSignIn intends.
 *
 * So this is not a tuning knob; it is what makes the error handling
 * reachable. It must stay comfortably under 7s: the remaining budget has to
 * cover the OIDC token mint above and the handler's own work. Observed cold
 * pablo-backend boot is ~12s, well past anything that would fit, so a cold
 * backend is always going to hit this — the point is to hit it as a timeout
 * we handle rather than as a deadline that kills us.
 */
const BACKEND_TIMEOUT_MS = 3500;

/**
 * Get the Pablo backend URL from environment or derive from project.
 */
function getBackendUrl(): string {
  return process.env.PABLO_BACKEND_URL || "";
}

/**
 * Make an authenticated request to the Pablo backend.
 * Uses OIDC identity token for service-to-service auth.
 */
async function callPabloApi<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const backendUrl = getBackendUrl();
  if (!backendUrl) {
    throw new HttpsError("internal", "PABLO_BACKEND_URL not configured");
  }

  const url = `${backendUrl}${path}`;
  const auth = new GoogleAuth();
  const client = await auth.getIdTokenClient(backendUrl);
  const response = await client.request({
    url,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    data: body,
    timeout: BACKEND_TIMEOUT_MS,
  });

  return response.data as T;
}

/**
 * beforeCreate blocking function
 *
 * Checks email against Pablo's allowlist via API.
 * Rejects with descriptive error if not allowlisted.
 *
 * Note: Admin SDK user creation (e.g., setup.sh seeding) bypasses blocking functions.
 */
export const beforeCreate = beforeUserCreated(async (event) => {
  const email = event.data?.email?.toLowerCase();

  if (!email) {
    throw new HttpsError("invalid-argument", "Email is required");
  }

  try {
    const result = await callPabloApi<{ allowed: boolean }>(
      "/api/ext/auth/check-allowlist",
      { email }
    );

    if (!result.allowed) {
      throw new HttpsError(
        "permission-denied",
        "Your email is not authorized to access this platform. Please contact your administrator."
      );
    }
  } catch (error) {
    if (error instanceof HttpsError) throw error;
    // If the backend is unreachable, fail closed (deny access). Unchanged by
    // BACKEND_TIMEOUT_MS — a cold backend was already denied here, by the
    // deadline. What changes is that it is denied with THIS message instead of
    // a bare BLOCKING_FUNCTION_ERROR_RESPONSE, so the person reading it can
    // tell "try again in a moment" from "you are not allowlisted".
    console.error("Failed to check allowlist:", error);
    throw new HttpsError(
      "internal",
      "Unable to verify authorization. Please try again later."
    );
  }

  return;
});

/**
 * beforeSignIn blocking function
 *
 * Checks user status via Pablo API.
 * Rejects if account is disabled.
 */
export const beforeSignIn = beforeUserSignedIn(async (event) => {
  const uid = event.data?.uid;

  if (!uid) {
    return; // Allow sign-in if no UID (shouldn't happen)
  }

  try {
    const result = await callPabloApi<{ disabled: boolean }>(
      "/api/ext/auth/check-status",
      { uid }
    );

    if (result.disabled) {
      throw new HttpsError(
        "permission-denied",
        "Your account has been disabled. Please contact your administrator."
      );
    }
  } catch (error) {
    if (error instanceof HttpsError) throw error;
    // Backend unreachable: allow the session shell, but a disabled account is
    // STILL blocked on every authenticated request by the backend's per-request
    // auth seam (_resolve_user in app/auth/service.py reads status=="disabled"
    // and raises 403 USER_DISABLED — covered by test_rejects_disabled_user). So
    // a fail-open login here yields only a token that can read no PHI; the
    // disable control does not depend on this check alone.
    //
    // Fail OPEN (unlike beforeCreate) on purpose: beforeSignIn runs on every
    // login, so a transient check-status blip must not lock every existing user
    // out of signing in. Login availability is the trade; PHI access is not.
    //
    // A COLD BACKEND REACHES HERE, and only because of BACKEND_TIMEOUT_MS. It
    // used to be caught by Identity Platform's 7s deadline instead, which kills
    // the invocation from outside — so this branch never ran and the first
    // sign-in after an idle period failed with BLOCKING_FUNCTION_ERROR_RESPONSE.
    // A slow backend is the commonest kind of unreachable, and it was the one
    // case this fail-open did not cover.
    console.error("Failed to check user status (failing open):", error);
  }

  return;
});
