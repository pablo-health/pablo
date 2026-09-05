// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/** Unbilled-sessions queue API client. Wraps `app.routes.billing_queue`. */

import type { UnbilledQueueResponse } from "@/types/billing"
import { get } from "./client"

export async function fetchUnbilledQueue(token?: string): Promise<UnbilledQueueResponse> {
  return get<UnbilledQueueResponse>("/api/billing/unbilled-sessions", token)
}
