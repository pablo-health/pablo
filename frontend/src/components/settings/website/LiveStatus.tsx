// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import type { PracticeSite } from "@/lib/api/practiceSite"
import { DOMAINS_SETTINGS_PATH } from "../paths"

/**
 * Where the website stands. "Live" only when the server names a live host:
 * that is a published version AND a website domain that works. Published with
 * no working domain is said as such, never as live.
 */
export function LiveStatus({ site }: { site: PracticeSite }) {
  if (site.live_host) {
    return (
      <p className="text-sm" data-testid="website-live">
        Live at{" "}
        <a
          href={`https://${site.live_host}`}
          target="_blank"
          rel="noopener noreferrer"
          className="font-medium underline"
        >
          {site.live_host}
        </a>
        . Changes can take a minute to appear.
      </p>
    )
  }
  if (site.live_version !== null) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="website-live">
        Published. It goes live when a website domain is active in{" "}
        <Link href={DOMAINS_SETTINGS_PATH} className="underline">
          Domains
        </Link>
        .
      </p>
    )
  }
  return (
    <p className="text-sm text-muted-foreground" data-testid="website-live">
      Not published yet.
    </p>
  )
}
