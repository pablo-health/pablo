// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Auth.js v5 catch-all route handler. Receives the full
 * `/api/auth/**` surface: sign-in redirects, the PKCE callback,
 * sign-out, session polling, and CSRF tokens.
 *
 * This file is only reached when `NEXT_PUBLIC_AUTH_PROVIDER=oidc`.
 * The Firebase auth routes (`/api/auth/native/*`,
 * `/api/auth/exchange-setup-token`) sit as siblings and are not
 * affected by this handler.
 */

import { handlers } from "@/lib/auth/oidc/config"

export const { GET, POST } = handlers
