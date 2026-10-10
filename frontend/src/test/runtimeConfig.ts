// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { RuntimeConfig } from "@/lib/config-provider"

/** A complete RuntimeConfig for tests, with the fields a test cares about overridden. */
export function testRuntimeConfig(overrides: Partial<RuntimeConfig> = {}): RuntimeConfig {
  return {
    apiUrl: "https://api.test",
    devMode: false,
    dataMode: "api",
    enableLocalAuth: false,
    firebaseProjectId: "test-project",
    firebaseApiKey: "test-key",
    firebaseAuthDomain: "test.firebaseapp.com",
    firebaseAppId: "test-app-id",
    ratingFeedbackRequiredBelow: 5,
    showVerificationBadges: false,
    introVideoUrl: "",
    passkeysEnabled: false,
    resubscribeUrl: "",
    publicBookingEnabled: false,
    googleCalendarEnabled: false,
    supportEmail: "",
    pabloEdition: "core",
    features: {},
    ...overrides,
  }
}
