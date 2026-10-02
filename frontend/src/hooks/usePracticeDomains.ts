// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useState } from "react"
import {
  addPracticeDomain,
  isUnsettled,
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

/** While a host is finishing setup, how often the list is asked again, and for how long. */
export const DOMAIN_POLL_MS = 15_000
export const DOMAIN_POLL_FOR_MS = 10 * 60_000
/** Check now rests this long after a click; the server dedupes runs anyway. */
export const CHECK_COOLDOWN_MS = 30_000

export const practiceDomainKeys = {
  all: ["practiceDomains"] as const,
}

/**
 * The practice's hosts. Given `pollUntil`, asks again every `DOMAIN_POLL_MS`
 * until then, for as long as any host is still pending or verifying — so a
 * host the server finishes turns Active on the page by itself.
 */
export function usePracticeDomains(pollUntil: number | null = null) {
  return useAuthQuery<PracticeDomainList>({
    queryKey: practiceDomainKeys.all,
    queryFn: () => listPracticeDomains(),
    refetchInterval: (query) => {
      if (pollUntil === null || Date.now() >= pollUntil) return false
      return query.state.data?.domains.some(isUnsettled) ? DOMAIN_POLL_MS : false
    },
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

/**
 * Check now, as the page runs it: the check itself, when it last answered, a
 * rest after each click, and how long to keep polling afterwards. A failed
 * check ends the rest at once, so trying again is never blocked.
 */
export function usePracticeDomainCheckRun() {
  const check = useCheckPracticeDomains()
  const [checkedAt, setCheckedAt] = useState<Date | null>(null)
  const [restingSince, setRestingSince] = useState<number | null>(null)
  const [pollUntil, setPollUntil] = useState<number | null>(null)

  useEffect(() => {
    if (restingSince === null) return
    const timer = setTimeout(() => setRestingSince(null), CHECK_COOLDOWN_MS)
    return () => clearTimeout(timer)
  }, [restingSince])

  function run() {
    setRestingSince(Date.now())
    check.mutate(undefined, {
      onSuccess: (data) => {
        setCheckedAt(new Date())
        setPollUntil(data.domains.some(isUnsettled) ? Date.now() + DOMAIN_POLL_FOR_MS : null)
      },
      onError: () => setRestingSince(null),
    })
  }

  return { check, run, checkedAt, resting: restingSince !== null, pollUntil }
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
