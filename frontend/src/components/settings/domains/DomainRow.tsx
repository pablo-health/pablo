// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import type { DnsRecord, DomainStatus, PracticeDomain, RecordCheck } from "@/lib/api/practiceDomains"
import { SettingsBadge } from "../ui"
import { CopyButton } from "./CopyButton"
import { isFinishingSetup, relativeHost } from "./records"

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

/**
 * A host whose records have all been in place for a while and is still not
 * active. Said in place of "Checking": the practice has nothing left to do in
 * its DNS, so the row tells it who can help instead. The server keeps checking,
 * and a host that goes active drops this by itself.
 */
const STUCK = { label: "Delayed", tone: "honey" } as const

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
  const stuck = domain.stuck === true && domain.status !== "active"
  const status = stuck ? STUCK : STATUS[domain.status]

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
      {stuck && domain.stuck_message && (
        <p role="status" className="mt-1.5 text-[12.5px] text-foreground" data-testid="domain-stuck">
          {domain.stuck_message}
        </p>
      )}
      {isFinishingSetup(domain) && (
        <p className="mt-1.5 text-[12.5px] text-foreground" data-testid="domain-finishing">
          Finishing setup — this usually takes a few minutes.
        </p>
      )}
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

/**
 * One record. Host is shown relative to the zone, as providers' forms ask for
 * it; the full name stays in the tooltip and for a screen reader. The check
 * cell's test id keeps the full name, which is what identifies the record.
 */
function RecordRow({ record, apex, checked }: { record: DnsRecord; apex?: string | null; checked: boolean }) {
  const hostRef = useRef<HTMLSpanElement>(null)
  const valueRef = useRef<HTMLSpanElement>(null)
  const host = relativeHost(record.name, apex)
  const which = `${record.type} record ${host}`

  return (
    <tr className="align-top">
      <td className="py-0.5">{record.type}</td>
      <td className="py-0.5 pr-2">
        <span className="flex items-start gap-1">
          <span ref={hostRef} className="min-w-0 break-all" title={record.name}>
            {host}
          </span>
          {host !== record.name && <span className="sr-only">{record.name}</span>}
          <CopyButton text={host} label={`Copy host for ${which}`} source={hostRef} />
        </span>
      </td>
      <td className="py-0.5 pr-2">
        <span className="flex items-start gap-1">
          <span ref={valueRef} className="min-w-0 break-all">
            {record.value}
          </span>
          <CopyButton text={record.value} label={`Copy value for ${which}`} source={valueRef} />
        </span>
      </td>
      {checked && (
        <td className="break-all py-0.5 font-sans" data-testid={`check-${record.type}-${record.name}`}>
          {record.check ? CHECK[record.check] : null}
          {record.check === "wrong" && record.found && record.found.length > 0 && (
            <span className="block font-mono text-muted-foreground">{record.found.join(", ")}</span>
          )}
        </td>
      )}
    </tr>
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
      {domain.status === "error" && !domain.stuck && (
        <p className="mb-1">The record couldn&apos;t be confirmed. Check it matches the one below.</p>
      )}
      {/* DNS providers' forms differ on the word: some say Host, some Name. */}
      <p className="mb-1">
        {records.length === 1 ? "Add this at your DNS provider." : "Add these at your DNS provider."} Some
        providers call Host &ldquo;Name&rdquo;.
      </p>
      {/* Scrolls inside the card on a narrow screen, never the page. */}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[30rem] table-fixed text-left" aria-label={`DNS records for ${domain.domain}`}>
          <thead>
            <tr className="text-[11px] uppercase tracking-[0.06em]">
              <th className="w-16 font-semibold">Type</th>
              <th className="w-[32%] font-semibold">Host</th>
              <th className="font-semibold">Value</th>
              {checked && <th className="w-28 font-semibold">Status</th>}
            </tr>
          </thead>
          <tbody className="font-mono text-foreground">
            {records.map((record) => (
              <RecordRow
                key={`${record.type}-${record.name}-${record.value}`}
                record={record}
                apex={domain.apex}
                checked={checked}
              />
            ))}
          </tbody>
        </table>
      </div>
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
