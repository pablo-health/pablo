// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One-click DNS setup for a practice's own domains, through Domain Connect —
 * against `backend/app/routes/domain_connect.py`.
 */

import { get, post } from "./client"
import type { DomainPurpose, PracticeDomain } from "./practiceDomains"

/** Why a template is not offered; see `ConnectReason` in the backend. */
export type DomainConnectReason =
  | "hosts_differ"
  | "records_pending"
  | "provider_unsupported"
  | "template_unsupported"
  | "lookup_failed"
  | "not_allowed"

export interface DomainConnectOffer {
  service_id: string
  purpose: DomainPurpose
  /** Whether the DNS provider has the template; null when it was not asked. */
  supported: boolean | null
  provider_name: string | null
  /** The signed link to the DNS provider; set only when it is offered. */
  url: string | null
  reason: DomainConnectReason | null
}

export interface DomainConnectDomain {
  apex: string
  offers: DomainConnectOffer[]
}

export interface DomainConnectList {
  /** Empty when the deployment does not offer one-click setup. */
  domains: DomainConnectDomain[]
}

export interface DomainConnectReturn {
  apex: string
  /** The DNS provider's error code, when it made no change. */
  error: string | null
  /** The practice's hosts, each record freshly checked. */
  domains: PracticeDomain[]
}

const CONNECT = "/api/practice/domains/connect"

export function listDomainConnect(): Promise<DomainConnectList> {
  return get<DomainConnectList>(CONNECT)
}

/** Hand the provider's return to the server, which checks the practice's DNS. */
export function returnFromDomainConnect(body: { state: string; error?: string }): Promise<DomainConnectReturn> {
  return post<DomainConnectReturn>(`${CONNECT}/return`, body)
}
