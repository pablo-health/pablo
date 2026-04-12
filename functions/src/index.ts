import { beforeUserCreated, beforeUserSignedIn } from "firebase-functions/v2/identity";
import { HttpsError } from "firebase-functions/v2/identity";
import { GoogleAuth } from "google-auth-library";

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
    // If the backend is unreachable, fail closed (deny access)
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
    // If backend unreachable, allow sign-in (fail open for existing users)
    // The backend auth middleware will still validate the JWT
    console.error("Failed to check user status:", error);
  }

  return;
});
