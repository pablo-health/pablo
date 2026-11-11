// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The two session-cookie endpoints the route-protection middleware answers
 * itself. They live apart from `@/lib/auth-config` so a client component can
 * name them: that module reads the cookie signature key at import time and
 * refuses to load without it, which is right on the server and fatal in a
 * browser bundle.
 */

export const loginPath = "/api/login"
export const logoutPath = "/api/logout"
