// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The Firebase provider's auth UI surfaces, assembled into the
 * {@link AuthSurfaces} registry the route shells dispatch through.
 */

import type { AuthSurfaces } from "@/lib/auth/types"
import { FirebaseLoginScreen } from "./LoginScreen"
import { FirebaseNativeAuthScreen } from "./NativeAuthScreen"
import { FirebaseAuthActionScreen } from "./AuthActionScreen"
import { FirebaseMfaEnrollmentForm } from "./MfaEnrollmentForm"

export const firebaseAuthSurfaces: AuthSurfaces = {
  id: "firebase",
  LoginScreen: FirebaseLoginScreen,
  NativeAuthScreen: FirebaseNativeAuthScreen,
  AuthActionScreen: FirebaseAuthActionScreen,
  MfaEnrollmentForm: FirebaseMfaEnrollmentForm,
}
