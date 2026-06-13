// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { cache } from "react"
import { getUserStatus } from "./users"

/**
 * Per-request memoized user status for Server Components.
 *
 * The dashboard layout and page both gate on /api/users/me/status during
 * the same navigation render pass; React's cache() collapses those into a
 * single backend call per request. Client components should keep using
 * getUserStatus via React Query instead.
 */
export const getCachedUserStatus = cache(getUserStatus)
