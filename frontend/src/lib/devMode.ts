// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Whether the app should run in dev mode (skips auth/MFA, renders a mock
 * user). Requires NODE_ENV !== "production" in addition to DEV_MODE=true,
 * so the bypass is dead code in a production build even if DEV_MODE is set
 * on a production revision by mistake.
 */
export const IS_DEV_MODE =
  process.env.DEV_MODE === "true" && process.env.NODE_ENV !== "production"
