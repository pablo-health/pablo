import { beforeUserCreated, beforeUserSignedIn } from "firebase-functions/v2/identity";
import { initializeApp } from "firebase-admin/app";
import { getFirestore } from "firebase-admin/firestore";
import { HttpsError } from "firebase-functions/v2/identity";

initializeApp();

/**
 * beforeCreate blocking function
 *
 * Checks email against allowed_emails Firestore collection.
 * Rejects with descriptive error if not allowlisted.
 *
 * Note: Admin SDK user creation (e.g., setup.sh seeding) bypasses blocking functions.
 */
export const beforeCreate = beforeUserCreated(async (event) => {
  const email = event.data?.email?.toLowerCase();

  if (!email) {
    throw new HttpsError("invalid-argument", "Email is required");
  }

  const db = getFirestore();
  const allowlistDoc = await db.collection("allowed_emails").doc(email).get();

  if (!allowlistDoc.exists) {
    throw new HttpsError(
      "permission-denied",
      "Your email is not authorized to access this platform. Please contact your administrator."
    );
  }

  // Allow the user creation to proceed
  return;
});

/**
 * beforeSignIn blocking function
 *
 * Checks user status in users Firestore collection.
 * Rejects if status === "disabled".
 */
export const beforeSignIn = beforeUserSignedIn(async (event) => {
  const uid = event.data?.uid;

  if (!uid) {
    return; // Allow sign-in if no UID (shouldn't happen)
  }

  const db = getFirestore();
  const userDoc = await db.collection("users").doc(uid).get();

  if (!userDoc.exists) {
    return; // New user, no status to check yet (will be auto-provisioned by backend)
  }

  const userData = userDoc.data();
  if (userData?.status === "disabled") {
    throw new HttpsError(
      "permission-denied",
      "Your account has been disabled. Please contact your administrator."
    );
  }

  // Allow the sign-in to proceed
  return;
});
