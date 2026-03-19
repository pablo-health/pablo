// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { type FirebaseApp, getApps, initializeApp } from "firebase/app"
import { type Auth, getAuth } from "firebase/auth"

export interface FirebaseConfig {
  apiKey: string
  authDomain: string
  projectId: string
  appId: string
}

let _auth: Auth | undefined

export function initFirebase(config: FirebaseConfig): Auth {
  if (!_auth) {
    const app: FirebaseApp =
      getApps().length === 0 ? initializeApp(config) : getApps()[0]
    _auth = getAuth(app)
  }
  return _auth
}

export function getFirebaseAuth(): Auth {
  if (!_auth) {
    throw new Error("Firebase not initialized. Wrap your app in ConfigProvider and AuthProvider.")
  }
  return _auth
}

/**
 * Set the tenant ID on the Firebase Auth instance.
 * Must be called BEFORE signInWithPopup/signInWithEmailAndPassword.
 * When set, the resulting JWT will include a firebase.tenant claim.
 * Pass null to clear (for platform admin auth).
 */
export function setFirebaseTenantId(tenantId: string | null): void {
  const auth = getFirebaseAuth()
  auth.tenantId = tenantId
}
