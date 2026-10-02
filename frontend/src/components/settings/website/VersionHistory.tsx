// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Button } from "@/components/ui/button"
import type { SiteVersion } from "@/lib/api/practiceSite"
import { SettingsBadge, SettingsCard } from "../ui"
import { describeFiles, formatWhen } from "./format"

interface VersionHistoryProps {
  versions: SiteVersion[]
  canManage: boolean
  busy: boolean
  onRollBack: (version: number) => void
}

/** The published versions kept, newest first; any but the current one can be put back. */
export function VersionHistory({ versions, canManage, busy, onRollBack }: VersionHistoryProps) {
  if (versions.length === 0) return null
  return (
    <SettingsCard title="Versions" description="The last 10 published versions are kept.">
      <ul aria-label="Published versions" className="divide-y divide-border">
        {versions.map((v) => (
          <li
            key={v.version}
            data-testid={`website-version-${v.version}`}
            className="flex items-center justify-between gap-3 py-2.5"
          >
            <div className="min-w-0">
              <p className="text-sm font-medium">Version {v.version}</p>
              <p className="text-[12.5px] text-muted-foreground">
                Published {formatWhen(v.published_at)} · {describeFiles(v.file_count, v.total_bytes)}
              </p>
            </div>
            {v.is_live ? (
              <SettingsBadge tone="sage">Current</SettingsBadge>
            ) : (
              canManage && (
                <Button size="sm" variant="outline" disabled={busy} onClick={() => onRollBack(v.version)}>
                  Roll back
                </Button>
              )
            )}
          </li>
        ))}
      </ul>
    </SettingsCard>
  )
}
