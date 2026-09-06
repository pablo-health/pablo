// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { fetchUnbilledQueue } from "@/lib/api/billing"
import { queryKeys } from "@/lib/api/queryKeys"
import type { UnbilledQueueResponse } from "@/types/billing"
import { useAuthQuery } from "./useAuthQuery"

export function useUnbilledQueue(token?: string) {
  return useAuthQuery<UnbilledQueueResponse>({
    queryKey: queryKeys.billing.unbilledQueue(),
    queryFn: () => fetchUnbilledQueue(token),
  })
}
