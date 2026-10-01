// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  addPracticeDomain,
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
