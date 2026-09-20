// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Instrument catalogue and licence API functions.
 *
 * See backend/app/routes/instrument_licenses.py. One read, because the
 * settings section and the form builder are asking the same question about
 * the same list; two writes, because recording permission and withdrawing it
 * are both acts the practice takes.
 */

import type {
  AttestInstrumentInput,
  Instrument,
  InstrumentAttestation,
} from "@/types/instruments"
import { del, get, post } from "./client"

const ENDPOINT = "/api/intake/instruments"

/** Every instrument the engine knows, and what this practice may do with it. */
export async function listInstruments(token?: string): Promise<Instrument[]> {
  return get<Instrument[]>(ENDPOINT, token)
}

/** Record that this practice holds the permission an instrument requires. */
export async function attestInstrument(
  code: string,
  input: AttestInstrumentInput = {},
  token?: string
): Promise<InstrumentAttestation> {
  return post<InstrumentAttestation>(`${ENDPOINT}/${code}/attestation`, input, token)
}

/**
 * Withdraw the permission in force for an instrument.
 *
 * Forms already published that ask it keep working; this decides what may go
 * on a new one.
 */
export async function revokeInstrumentAttestation(
  code: string,
  token?: string
): Promise<InstrumentAttestation> {
  return del<InstrumentAttestation>(`${ENDPOINT}/${code}/attestation`, token)
}
