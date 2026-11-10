// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { PracticeSite } from "@/lib/api/practiceSite"
import { formatAgo, formatDay } from "./format"

/**
 * Two quiet lines under where the website stands: since when the live version
 * has been up, and when a draft was last previewed. "Live since" only when the
 * server names a live host, as in `LiveStatus`; published with no working
 * domain is dated as published. Either line is left out while it has no date.
 */
export function SiteTimes({ site }: { site: PracticeSite }) {
  const published = site.live_version !== null ? site.live_published_at : null
  if (!published && !site.last_previewed_at) return null
  return (
    <div className="mt-1 space-y-0.5 text-[12.5px] text-muted-foreground" data-testid="website-times">
      {published && (
        <p data-testid="website-live-since">
          {site.live_host ? "Live since" : "Published"} {formatDay(published)}.
        </p>
      )}
      {site.last_previewed_at && (
        <p data-testid="website-last-previewed">Draft last previewed {formatAgo(site.last_previewed_at)}.</p>
      )}
    </div>
  )
}
