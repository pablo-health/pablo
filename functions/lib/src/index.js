"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.beforeSignIn = exports.beforeCreate = void 0;
const identity_1 = require("firebase-functions/v2/identity");
const app_1 = require("firebase-admin/app");
const firestore_1 = require("firebase-admin/firestore");
const identity_2 = require("firebase-functions/v2/identity");
(0, app_1.initializeApp)();
/**
 * beforeCreate blocking function
 *
 * Checks email against allowed_emails Firestore collection.
 * Rejects with descriptive error if not allowlisted.
 *
 * Note: Admin SDK user creation (e.g., setup.sh seeding) bypasses blocking functions.
 */
exports.beforeCreate = (0, identity_1.beforeUserCreated)(async (event) => {
    const email = event.data?.email?.toLowerCase();
    if (!email) {
        throw new identity_2.HttpsError("invalid-argument", "Email is required");
    }
    const db = (0, firestore_1.getFirestore)();
    const allowlistDoc = await db.collection("allowed_emails").doc(email).get();
    if (!allowlistDoc.exists) {
        throw new identity_2.HttpsError("permission-denied", "Your email is not authorized to access this platform. Please contact your administrator.");
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
exports.beforeSignIn = (0, identity_1.beforeUserSignedIn)(async (event) => {
    const uid = event.data?.uid;
    if (!uid) {
        return; // Allow sign-in if no UID (shouldn't happen)
    }
    const db = (0, firestore_1.getFirestore)();
    const userDoc = await db.collection("users").doc(uid).get();
    if (!userDoc.exists) {
        return; // New user, no status to check yet (will be auto-provisioned by backend)
    }
    const userData = userDoc.data();
    if (userData?.status === "disabled") {
        throw new identity_2.HttpsError("permission-denied", "Your account has been disabled. Please contact your administrator.");
    }
    // Allow the sign-in to proceed
    return;
});
//# sourceMappingURL=index.js.map