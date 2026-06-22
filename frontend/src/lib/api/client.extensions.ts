// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ApiErrorInterceptor } from "./client"

/**
 * Extension slot for the API client's error path.
 *
 * Ships empty here. A downstream build (e.g. a deployment overlay) may
 * overwrite *this file only* to observe failed responses and ride extra
 * fields onto the thrown `ApiError` — without forking `client.ts`. Each
 * interceptor runs before the error body is consumed and returns a partial
 * that `apiClient` assigns onto the `ApiError` (or null to do nothing).
 *
 * The empty default makes the OSS client behave exactly as if there were no
 * interceptors at all.
 */
export const apiErrorInterceptors: ApiErrorInterceptor[] = []
