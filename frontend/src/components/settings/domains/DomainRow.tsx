// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import type { DnsRecord, DomainStatus, PracticeDomain, RecordCheck } from "@/lib/api/practiceDomains"
import { SettingsBadge } from "../ui"

/**
 * What each status is called on screen. Only `active` says the domain works:
 * the status is set by whatever serves the host, after it has checked, so the
 * page never reads readiness off anything else.
 */
const STATUS: Record<DomainStatus, { label: string; tone: "sage" | "honey" | "mute" }> = {
  pending: { label: "Waiting for DNS", tone: "mute" },
  verifying: { label: "Checking", tone: "honey" },
  active: { label: "Active", tone: "sage" },
  error: { label: "Not working", tone: "honey" },
}

interface DomainRowProps {
  domain: PracticeDomain
  canManage: boolean
  busy: boolean
  onMakePrimary: (domain: string) => void
  onRemove: (domain: string) => void
}

/** One host: its status, whether it is primary, and the DNS record it needs. */
export function DomainRow({ domain, canManage, busy, onMakePrimary, onRemove }: DomainRowProps) {
  const [confirming, setConfirming] = useState(false)
  const status = STATUS[domain.status]

  return (
    <li
      className="border-t border-border py-3 first:border-t-0 first:pt-1"
      data-testid={`domain-row-${domain.domain}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="truncate text-sm font-semibold text-foreground">{domain.domain}</span>
          {domain.is_primary && <SettingsBadge tone="sky">Primary</SettingsBadge>}
          <SettingsBadge tone={status.tone}>{status.label}</SettingsBadge>
        </div>
        {canManage && !confirming && (
          <div className="flex shrink-0 items-center gap-1">
            {domain.status === "active" && !domain.is_primary && (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => onMakePrimary(domain.domain)}>
                Make primary
              </Button>
            )}
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirming(true)}>
              Remove
            </Button>
          </div>
        )}
        {canManage && confirming && (
          <div className="flex shrink-0 items-center gap-2">
            <span className="text-[12.5px] text-muted-foreground">Remove {domain.domain}?</span>
            <Button
              size="sm"
              variant="destructive"
              disabled={busy}
              onClick={() => {
                setConfirming(false)
                onRemove(domain.domain)
              }}
            >
              Remove
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
              Keep
            </Button>
          </div>
        )}
      </div>
      <DnsInstructions domain={domain} />
    </li>
  )
}

/**
 * What a check found, in words that claim no more than that: a record being
 * there is not the domain working, so nothing here says "working" or "done".
 */
const CHECK: Record<RecordCheck, string> = {
  ok: "Found",
  missing: "Not found yet",
  wrong: "Doesn't match",
  unknown: "Couldn't check",
}

/** The record that points the host itself here, as opposed to the others it needs. */
function isPointingRecord(domain: PracticeDomain, record: DnsRecord): boolean {
  return record.name === domain.domain && ["A", "AAAA", "CNAME"].includes(record.type)
}

/**
 * The records still worth showing. All of them until the host is active; after
 * that, an active host's own record has evidently done its job, so only the
 * others — and any a check found out of place — stay on screen.
 */
function recordsToShow(domain: PracticeDomain): DnsRecord[] {
  if (domain.status !== "active") return domain.dns_records
  return domain.dns_records.filter(
    (record) => !isPointingRecord(domain, record) || (record.check != null && record.check !== "ok"),
  )
}

function DnsInstructions({ domain }: { domain: PracticeDomain }) {
  const records = recordsToShow(domain)
  if (records.length === 0) {
    if (domain.status === "active") return null
    return (
      <p className="mt-1.5 text-[12.5px] text-muted-foreground">
        Point {domain.domain} at this server in your DNS settings.
      </p>
    )
  }
  const checked = records.some((record) => record.check != null)
  return (
    <div className="mt-2 text-[12.5px] text-muted-foreground">
      {domain.status === "error" && (
        <p className="mb-1">The record couldn&apos;t be confirmed. Check it matches the one below.</p>
      )}
      <p className="mb-1">
        {records.length === 1 ? "Add this record at your DNS provider:" : "Add these records at your DNS provider:"}
      </p>
      <table className="w-full table-fixed text-left" aria-label={`DNS records for ${domain.domain}`}>
        <thead>
          <tr className="text-[11px] uppercase tracking-[0.06em]">
            <th className="w-20 font-semibold">Type</th>
            <th className="font-semibold">Name</th>
            <th className="font-semibold">Value</th>
            {checked && <th className="w-32 font-semibold">Check</th>}
          </tr>
        </thead>
        <tbody className="font-mono text-foreground">
          {records.map((record) => (
            <tr key={`${record.type}-${record.name}-${record.value}`}>
              <td>{record.type}</td>
              <td className="break-all pr-2">{record.name}</td>
              <td className="break-all">{record.value}</td>
              {checked && (
                <td className="break-all pl-2 font-sans" data-testid={`check-${record.type}-${record.name}`}>
                  {record.check ? CHECK[record.check] : null}
                  {record.check === "wrong" && record.found && record.found.length > 0 && (
                    <span className="block font-mono text-muted-foreground">{record.found.join(", ")}</span>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {domain.alias_alternative && (
        <p className="mt-1.5" data-testid="alias-alternative">
          If your DNS provider offers ALIAS or ANAME records, one pointing at{" "}
          <span className="font-mono text-foreground">{domain.alias_alternative}</span> works
          instead.
        </p>
      )}
    </div>
  )
}
