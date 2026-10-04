// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useRef } from "react"
import type { HostedAddresses as Hosted, PracticeDomain } from "@/lib/api/practiceDomains"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { SettingsBadge, SettingsCard } from "../ui"
import { CopyButton } from "./CopyButton"

/**
 * The practice's addresses under the deployment's hosted domain
 * (`backend/app/portal/hosted.py`). Each says only what the data says: "Active"
 * when the address serves something today, what it waits on when it does not,
 * and where it sends visitors once the practice's own domain is its primary.
 */
export function HostedAddresses({ hosted, domains }: { hosted: Hosted; domains: PracticeDomain[] }) {
  const people = usePeopleTerm()
  const primary = (purpose: PracticeDomain["purpose"]) =>
    domains.find((d) => d.purpose === purpose && d.is_primary && d.status === "active")?.domain ?? null

  return (
    <SettingsCard
      title="Your addresses"
      description="Ready-made addresses for your practice, with nothing to set up. A domain of your own can replace them."
    >
      <ul aria-label="Your addresses">
        <HostedRow
          testId="hosted-address-portal"
          label={`${people.One} portal`}
          host={hosted.portal_host}
          waitingFor={hosted.portal_on ? null : `Works once your ${people.one} portal is turned on.`}
          movedTo={primary("portal")}
        />
        <HostedRow
          testId="hosted-address-site"
          label="Website"
          host={hosted.site_host}
          waitingFor={hosted.site_live ? null : "Works once you publish your website."}
          movedTo={primary("site")}
        />
      </ul>
    </SettingsCard>
  )
}

interface HostedRowProps {
  testId: string
  label: string
  host: string
  /** What the address waits on before it serves anything; `null` when it serves now. */
  waitingFor: string | null
  /** The practice's own working primary of this purpose, where visitors are sent. */
  movedTo: string | null
}

function HostedRow({ testId, label, host, waitingFor, movedTo }: HostedRowProps) {
  const hostRef = useRef<HTMLSpanElement>(null)

  return (
    <li className="border-t border-border py-3 first:border-t-0 first:pt-1" data-testid={testId}>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <span className="text-[12.5px] text-muted-foreground">{label}</span>
        <span className="flex min-w-0 items-center gap-1">
          <span ref={hostRef} className="break-all text-sm font-semibold text-foreground">
            {host}
          </span>
          <CopyButton
            text={`https://${host}`}
            label={`Copy ${label.toLowerCase()} address`}
            source={hostRef}
          />
        </span>
        {!waitingFor && !movedTo && <SettingsBadge tone="sage">Active</SettingsBadge>}
      </div>
      {waitingFor ? (
        <p className="mt-1.5 text-[12.5px] text-muted-foreground">{waitingFor}</p>
      ) : (
        movedTo && <p className="mt-1.5 text-[12.5px] text-muted-foreground">Sends visitors to {movedTo}.</p>
      )}
    </li>
  )
}
