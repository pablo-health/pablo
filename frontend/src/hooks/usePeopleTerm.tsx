// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { createContext, useContext, useMemo } from "react"
import type { QueryClient } from "@tanstack/react-query"
import {
  getPeopleTerm,
  setPeopleTerm,
  setPracticePeopleTerm,
  type PeopleTermState,
} from "@/lib/api/peopleTerm"
import { queryKeys } from "@/lib/api/queryKeys"
import { DEFAULT_PEOPLE_TERM, peopleWords, type PeopleTerm, type PeopleWords } from "@/lib/peopleTerm"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export const PEOPLE_TERM_QUERY_KEY = ["user", "people-term"] as const

// Outside a provider (a test rendering one component, a page before sign-in)
// the words are the default rather than an error.
const PeopleTermContext = createContext<PeopleWords>(peopleWords(DEFAULT_PEOPLE_TERM))

/**
 * Supplies the clinician's word to everything below it.
 *
 * `initialTerm` is the word the server already resolved for the page (it rides
 * on the user-status response), so the first paint uses the right word rather
 * than flashing the default. The query keeps it current after a change in
 * Settings.
 */
export function PeopleTermProvider({
  initialTerm,
  children,
}: {
  initialTerm?: PeopleTerm | null
  children: React.ReactNode
}) {
  const { data } = usePeopleTermState()
  const term = data?.people_term ?? initialTerm ?? DEFAULT_PEOPLE_TERM
  const words = useMemo(() => peopleWords(term), [term])
  return <PeopleTermContext.Provider value={words}>{children}</PeopleTermContext.Provider>
}

/**
 * The word for the people this clinician sees: `one`, `many`, `One`, `Many`.
 *
 * Use it for every clinician-facing "client(s)" or "patient(s)". Copy a client
 * reads (the portal, booking pages) speaks to them as "you" and doesn't need it.
 */
export function usePeopleTerm(): PeopleWords {
  return useContext(PeopleTermContext)
}

/** The word, what decided it, and who may change the practice default. */
export function usePeopleTermState() {
  return useAuthQuery({
    queryKey: PEOPLE_TERM_QUERY_KEY,
    queryFn: () => getPeopleTerm(),
    staleTime: 5 * 60 * 1000,
  })
}

const storeResult = {
  onSuccess: (data: PeopleTermState, _term: PeopleTerm | null, queryClient: QueryClient) => {
    queryClient.setQueryData(PEOPLE_TERM_QUERY_KEY, data)
  },
}

/** Set the clinician's own word, or null to go back to the default. */
export function useSetPeopleTerm() {
  return useAuthMutation({
    mutationFn: (term: PeopleTerm | null) => setPeopleTerm(term),
    ...storeResult,
    // The choice is stored with the other preferences; a stale copy of them
    // saved later would otherwise write the old choice back.
    invalidateKeys: [queryKeys.user.preferences()],
  })
}

/** Set the practice default (owner only), or null to clear it. */
export function useSetPracticePeopleTerm() {
  return useAuthMutation({
    mutationFn: (term: PeopleTerm | null) => setPracticePeopleTerm(term),
    ...storeResult,
  })
}
