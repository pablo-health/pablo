// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import type { DomainStatus, PracticeDomain } from "@/lib/api/practiceDomains"
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
      {domain.status !== "active" && <DnsInstructions domain={domain} />}
    </li>
  )
}

function DnsInstructions({ domain }: { domain: PracticeDomain }) {
  if (domain.dns_records.length === 0) {
    return (
      <p className="mt-1.5 text-[12.5px] text-muted-foreground">
        Point {domain.domain} at this server in your DNS settings.
      </p>
    )
  }
  return (
    <div className="mt-2 text-[12.5px] text-muted-foreground">
      {domain.status === "error" && (
        <p className="mb-1">The record couldn&apos;t be confirmed. Check it matches the one below.</p>
      )}
      <p className="mb-1">Add this record at your DNS provider:</p>
      <table className="w-full table-fixed text-left" aria-label={`DNS records for ${domain.domain}`}>
        <thead>
          <tr className="text-[11px] uppercase tracking-[0.06em]">
            <th className="w-20 font-semibold">Type</th>
            <th className="font-semibold">Name</th>
            <th className="font-semibold">Value</th>
          </tr>
        </thead>
        <tbody className="font-mono text-foreground">
          {domain.dns_records.map((record) => (
            <tr key={`${record.type}-${record.name}-${record.value}`}>
              <td>{record.type}</td>
              <td className="break-all pr-2">{record.name}</td>
              <td className="break-all">{record.value}</td>
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
