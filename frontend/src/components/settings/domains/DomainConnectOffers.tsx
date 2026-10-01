// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Button } from "@/components/ui/button"
import type { DomainConnectDomain } from "@/lib/api/domainConnect"
import type { DomainPurpose } from "@/lib/api/practiceDomains"

interface DomainConnectOffersProps {
  purpose: DomainPurpose
  domains: DomainConnectDomain[] | undefined
}

/**
 * The one-click setup links for one section of the page. Only the offers the
 * server signed a link for are shown; everything else keeps the records to
 * copy by hand, with nothing added to say so.
 *
 * The link goes to the DNS provider, which shows the change and asks the
 * practice to approve it there — so the words promise only that the provider
 * can add the records, not that they are added.
 */
export function DomainConnectOffers({ purpose, domains }: DomainConnectOffersProps) {
  const offers = (domains ?? []).flatMap(({ apex, offers }) =>
    offers.flatMap(({ purpose: offerPurpose, url, provider_name: provider }) =>
      offerPurpose === purpose && url && provider ? [{ apex, url, provider }] : [],
    ),
  )
  if (offers.length === 0) return null

  return (
    <ul className="mb-3 space-y-2" aria-label="Set up at your DNS provider">
      {offers.map(({ apex, url, provider }) => (
        <li key={url} className="flex flex-wrap items-center gap-3" data-testid={`domain-connect-${apex}`}>
          <span className="text-[12.5px] text-muted-foreground">
            {provider} can add the records for {apex} for you.
          </span>
          <Button size="sm" variant="outline" asChild>
            <a href={url}>Set up with {provider}</a>
          </Button>
        </li>
      ))}
    </ul>
  )
}
