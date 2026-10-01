// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useState } from "react"
import {
  addPracticeDomain,
  checkPracticeDomains,
  describePracticeDomain,
  listPracticeDomains,
  makePracticeDomainPrimary,
  removePracticeDomain,
  type AddPracticeDomain,
  type DomainName,
  type PracticeDomainList,
} from "@/lib/api/practiceDomains"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

const DESCRIBE_DELAY_MS = 250

export const practiceDomainKeys = {
  all: ["practiceDomains"] as const,
}

export function usePracticeDomains() {
  return useAuthQuery<PracticeDomainList>({
    queryKey: practiceDomainKeys.all,
    queryFn: () => listPracticeDomains(),
  })
}

/**
 * How the server reads a typed name, asked once typing pauses. `enabled` off,
 * or an empty name, asks nothing. `undefined` until there is an answer for
 * the name as it stands, including when the server refuses the name.
 */
export function useDescribePracticeDomain(domain: string, enabled: boolean) {
  const [settled, setSettled] = useState(domain)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(domain), DESCRIBE_DELAY_MS)
    return () => clearTimeout(timer)
  }, [domain])

  const query = useAuthQuery<DomainName>({
    // Not under practiceDomainKeys.all: changing the list says nothing new
    // about how a name is read.
    queryKey: ["practiceDomainName", settled],
    queryFn: () => describePracticeDomain(settled),
    enabled: enabled && settled.length > 0 && settled === domain,
    retry: false,
    staleTime: Infinity,
  })
  // While typing, the last answer is about a name that is no longer there.
  return settled === domain ? query.data : undefined
}

export function useAddPracticeDomain() {
  return useAuthMutation<PracticeDomainList, AddPracticeDomain>({
    mutationFn: (body) => addPracticeDomain(body),
    invalidateKeys: [practiceDomainKeys.all],
  })
}

/**
 * Check the practice's DNS. The answer replaces the cached list rather than
 * invalidating it, since only this answer carries what was found per record.
 */
export function useCheckPracticeDomains() {
  return useAuthMutation<PracticeDomainList, void>({
    mutationFn: () => checkPracticeDomains(),
    onSuccess: (data, _variables, queryClient) => {
      queryClient.setQueryData(practiceDomainKeys.all, data)
    },
  })
}

export function useMakePracticeDomainPrimary() {
  return useAuthMutation<PracticeDomainList, string>({
    mutationFn: (domain) => makePracticeDomainPrimary(domain),
    invalidateKeys: [practiceDomainKeys.all],
  })
}

export function useRemovePracticeDomain() {
  return useAuthMutation<PracticeDomainList, string>({
    mutationFn: (domain) => removePracticeDomain(domain),
    invalidateKeys: [practiceDomainKeys.all],
  })
}
