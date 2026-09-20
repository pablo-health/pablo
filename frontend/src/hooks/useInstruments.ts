// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  attestInstrument,
  listInstruments,
  revokeInstrumentAttestation,
} from "@/lib/api/instruments"
import { queryKeys } from "@/lib/api/queryKeys"
import type { AttestInstrumentInput, Instrument } from "@/types/instruments"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/**
 * Every instrument the engine knows, and what this practice may do with it.
 *
 * One query behind both the settings section and the form builder's measure
 * picker. Recording or withdrawing permission invalidates it, so the picker
 * stops greying an instrument out without the practice reloading anything.
 */
export function useInstruments(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.instruments.list(),
    queryFn: (): Promise<Instrument[]> => listInstruments(token),
    staleTime: 60 * 1000,
  })
}

export function useAttestInstrument(token?: string) {
  return useAuthMutation({
    mutationFn: ({ code, input }: { code: string; input: AttestInstrumentInput }) =>
      attestInstrument(code, input, token),
    invalidateKeys: [queryKeys.instruments.all],
  })
}

export function useRevokeInstrumentAttestation(token?: string) {
  return useAuthMutation({
    mutationFn: (code: string) => revokeInstrumentAttestation(code, token),
    invalidateKeys: [queryKeys.instruments.all],
  })
}
