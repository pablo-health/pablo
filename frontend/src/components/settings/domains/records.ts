// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import {
  isUnsettled,
  type DnsRecord,
  type PracticeDomain,
  type PracticeDomainList,
} from "@/lib/api/practiceDomains"

function bare(name: string): string {
  return name.trim().replace(/\.$/, "").toLowerCase()
}

/**
 * A record's name as a DNS provider's form asks for it: relative to the zone,
 * which is the registrable domain the host sits under. `@` for the domain
 * itself, `portal` for `portal.example.com`, `_acme-challenge.portal` for the
 * certificate record under it.
 *
 * A name that is not under `apex` — or no `apex` at all — comes back whole:
 * the full name is still correct anywhere a provider accepts one, and cutting
 * a name we cannot place would give the practice a wrong record.
 */
export function relativeHost(name: string, apex: string | null | undefined): string {
  if (!apex) return name
  const full = bare(name)
  const zone = bare(apex)
  if (!zone) return name
  if (full === zone) return "@"
  if (full.endsWith(`.${zone}`)) return full.slice(0, -(zone.length + 1))
  return name
}

function recordKey(record: DnsRecord): string {
  return `${record.type}|${record.name}|${record.value}`
}

/**
 * The list as it stands, with each record's last check put back on it.
 *
 * Only the answer to a check carries what was found per record; a later list
 * (the page polling while setup finishes) does not, and would otherwise wipe
 * the results the practice just saw. A host whose status has moved since the
 * check gets none back: what was found before it moved no longer describes it.
 */
export function withLastCheck(
  list: PracticeDomainList,
  checked: PracticeDomainList | undefined,
): PracticeDomainList {
  if (!checked) return list
  const byHost = new Map(checked.domains.map((d) => [d.domain, d]))
  return {
    ...list,
    domains: list.domains.map((domain) => {
      const before = byHost.get(domain.domain)
      if (!before || before.status !== domain.status) return domain
      const found = new Map(before.dns_records.map((r) => [recordKey(r), r]))
      return {
        ...domain,
        dns_records: domain.dns_records.map((record) => {
          if (record.check != null) return record
          const prior = found.get(recordKey(record))
          return prior?.check != null ? { ...record, check: prior.check, found: prior.found } : record
        }),
      }
    }),
  }
}

/**
 * Every record the host needs was found, and it is still not active: what is
 * left is the server's, not the practice's (a certificate, routing). A stuck
 * host says something of its own instead.
 */
export function isFinishingSetup(domain: PracticeDomain): boolean {
  return (
    isUnsettled(domain) &&
    domain.stuck !== true &&
    domain.dns_records.length > 0 &&
    domain.dns_records.every((record) => record.check === "ok")
  )
}
