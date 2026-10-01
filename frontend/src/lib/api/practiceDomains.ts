// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The hosts a practice serves its client portal and website from — against
 * `backend/app/routes/practice_domains.py`. Every call answers with the
 * practice's whole list, so the page never has to merge a change by hand.
 */

import { del, get, post } from "./client"

export type DomainPurpose = "portal" | "site"
export type DomainStatus = "pending" | "verifying" | "active" | "error"

export interface DnsRecord {
  type: string
  name: string
  value: string
}

export interface PracticeDomain {
  domain: string
  purpose: DomainPurpose
  status: DomainStatus
  is_primary: boolean
  verified_at: string | null
  created_at: string
  /** Empty when the deployment names no single target. */
  dns_records: DnsRecord[]
}

export interface PracticeDomainList {
  domains: PracticeDomain[]
}

export interface AddPracticeDomain {
  domain: string
  purpose: DomainPurpose
  /** Website only. Left out, the server adds `www.` for a two-label name. */
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
