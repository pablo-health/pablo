// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The hosts a practice serves its client portal and website from — against
 * `backend/app/routes/practice_domains.py`. Every call answers with the
 * practice's whole list, so the page never has to merge a change by hand.
 */

import { del, get, post } from "./client"

export type DomainPurpose = "portal" | "site"
export type DomainStatus = "pending" | "verifying" | "active" | "error"

/**
 * What a DNS check found for one record: there and matching, not there, there
 * with another value, or no answer in time to tell.
 */
export type RecordCheck = "ok" | "missing" | "wrong" | "unknown"

export interface DnsRecord {
  type: string
  /** The full name, e.g. `_pablo-verify.example.com`. */
  name: string
  value: string
  /** Set only in the answer to a check. */
  check?: RecordCheck | null
  /** With `check`: the values found at that name and type. */
  found?: string[] | null
}

export interface PracticeDomain {
  domain: string
  purpose: DomainPurpose
  status: DomainStatus
  is_primary: boolean
  verified_at: string | null
  created_at: string
  /**
   * The host's own record first; then, when known, its certificate record and
   * — on one host per registrable domain — the domain's ownership TXT and
   * DKIM records. Empty when the deployment names no single target and
   * nothing else is known yet.
   */
  dns_records: DnsRecord[]
  /** For a bare domain shown address records: what an ALIAS/ANAME record
   * could point at instead, where the DNS provider offers one. */
  alias_alternative?: string | null
  /** The registrable domain the host sits under, e.g. `example.co.uk`. */
  apex?: string | null
  /** When the domain's ownership record was last found. */
  apex_verified_at?: string | null
}

export interface PracticeDomainList {
  domains: PracticeDomain[]
}

export interface AddPracticeDomain {
  domain: string
  purpose: DomainPurpose
  /** Website only. Left out, the server adds `www.` for a bare domain. */
  include_www?: boolean
}

const DOMAINS = "/api/practice/domains"

const one = (domain: string) => `${DOMAINS}/${encodeURIComponent(domain)}`

export function listPracticeDomains(): Promise<PracticeDomainList> {
  return get<PracticeDomainList>(DOMAINS)
}

export function addPracticeDomain(body: AddPracticeDomain): Promise<PracticeDomainList> {
  return post<PracticeDomainList>(DOMAINS, body)
}

/** Look every record up in DNS. The answer carries `check` on each record. */
export function checkPracticeDomains(): Promise<PracticeDomainList> {
  return post<PracticeDomainList>(`${DOMAINS}/check`, {})
}

export function makePracticeDomainPrimary(domain: string): Promise<PracticeDomainList> {
  return post<PracticeDomainList>(`${one(domain)}/primary`, {})
}

export function removePracticeDomain(domain: string): Promise<PracticeDomainList> {
  return del<PracticeDomainList>(one(domain))
}

/** Whether adding `domain` as a website brings `www.` by default: a two-label name. */
export function suggestsWww(domain: string): boolean {
  const host = domain.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/[/.]+$/, "")
  return !host.startsWith("www.") && host.split(".").filter(Boolean).length === 2
}
