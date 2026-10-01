// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  addPracticeDomain,
  checkPracticeDomains,
  listPracticeDomains,
  makePracticeDomainPrimary,
  removePracticeDomain,
  type AddPracticeDomain,
  type PracticeDomainList,
} from "@/lib/api/practiceDomains"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export const practiceDomainKeys = {
  all: ["practiceDomains"] as const,
}

export function usePracticeDomains() {
  return useAuthQuery<PracticeDomainList>({
    queryKey: practiceDomainKeys.all,
    queryFn: () => listPracticeDomains(),
  })
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
