// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  listDomainConnect,
  returnFromDomainConnect,
  type DomainConnectList,
  type DomainConnectReturn,
} from "@/lib/api/domainConnect"
import { practiceDomainKeys } from "./usePracticeDomains"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/**
 * The one-click setup links. Keyed under the domain list, so adding or
 * removing a host asks again. Each answer carries freshly signed links that
 * expire, so it is never served stale.
 */
export function useDomainConnect(enabled: boolean) {
  return useAuthQuery<DomainConnectList>({
    queryKey: [...practiceDomainKeys.all, "connect"],
    queryFn: () => listDomainConnect(),
    enabled,
    staleTime: 0,
    retry: false,
  })
}

/**
 * Coming back from the DNS provider. The answer carries a fresh DNS check, so
 * it replaces the cached list the way "Check now" does.
 */
export function useDomainConnectReturn() {
  return useAuthMutation<DomainConnectReturn, { state: string; error?: string }>({
    mutationFn: (body) => returnFromDomainConnect(body),
    onSuccess: (data, _variables, queryClient) => {
      queryClient.setQueryData(practiceDomainKeys.all, { domains: data.domains })
    },
  })
}
