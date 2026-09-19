// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { AlertCircle, ScrollText } from "lucide-react"
import {
  describeAuditResource,
  formatAuditAction,
  formatAuditTimestamp,
  isSomeoneElsesAction,
  summarizeUserAgent,
} from "../auditLogDisplay"
import { SettingsBadge, SettingsCard } from "../ui"
import { Button } from "@/components/ui/button"
import { useAuditLog } from "@/hooks/useAuditLog"
import type { AuditLogItem } from "@/lib/api/users"

function Row({ entry }: { entry: AuditLogItem }) {
  return (
    <tr className="border-t border-border align-top">
      <td className="whitespace-nowrap px-[22px] py-3 text-[13px] text-muted-foreground">
        {formatAuditTimestamp(entry.timestamp)}
      </td>
      <td className="px-3 py-3 text-[13px] font-medium text-foreground">
        <div className="flex items-center gap-2">
          {formatAuditAction(entry.action)}
          {isSomeoneElsesAction(entry) && (
            <SettingsBadge tone="mute">{formatAuditAction(entry.actor_type)}</SettingsBadge>
          )}
        </div>
      </td>
      <td className="px-3 py-3 font-mono text-[12px] leading-relaxed break-all text-muted-foreground">
        {describeAuditResource(entry)}
      </td>
      <td className="whitespace-nowrap px-3 py-3 font-mono text-[12px] text-muted-foreground">
        {entry.ip_address ?? "—"}
      </td>
      <td
        className="px-[22px] py-3 text-[12px] text-muted-foreground"
        title={entry.user_agent ?? undefined}
      >
        {summarizeUserAgent(entry.user_agent)}
      </td>
    </tr>
  )
}

/**
 * You > Your activity.
 *
 * The user-facing half of the HIPAA access record: every action recorded
 * against this account, newest first, with the request context that makes it
 * possible to recognise access you did not make.
 *
 * Two things this page deliberately does not do. It does not refresh itself
 * — reading the log is itself a recorded access, so a polling view would
 * fill with rows about being looked at. And it does not resolve an id to a
 * name: the trail is PHI-free by design, and a friendlier label here would
 * quietly change what this screen holds.
 */
export function ActivityPage() {
  const { entries, isLoading, isError, refetch, hasMore, loadMore, isLoadingMore } =
    useAuditLog()

  return (
    // Untitled on purpose: the settings shell already renders "Your
    // activity" and its one-liner above every page, and a card repeating
    // them puts the same heading on screen twice.
    <SettingsCard flush>
      {isError ? (
        <div className="flex items-start gap-3 px-[22px] py-8">
          <AlertCircle className="mt-0.5 h-[18px] w-[18px] shrink-0 text-muted-foreground" aria-hidden="true" />
          <div>
            <div className="text-sm font-semibold text-foreground">
              Your activity could not be loaded
            </div>
            <p className="mt-0.5 text-[13px] leading-relaxed text-muted-foreground">
              The record is intact; this page could not reach it.
            </p>
            <Button variant="outline" size="sm" className="mt-3" onClick={() => refetch()}>
              Try again
            </Button>
          </div>
        </div>
      ) : isLoading ? (
        <div className="px-[22px] py-8 text-[13px] text-muted-foreground">Loading your activity…</div>
      ) : entries.length === 0 ? (
        <div className="flex items-start gap-3 px-[22px] py-8">
          <ScrollText className="mt-0.5 h-[18px] w-[18px] shrink-0 text-muted-foreground" aria-hidden="true" />
          <div>
            <div className="text-sm font-semibold text-foreground">Nothing recorded yet</div>
            <p className="mt-0.5 text-[13px] leading-relaxed text-muted-foreground">
              Actions appear here as you work.
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="text-[11px] uppercase tracking-wide text-muted-foreground">
                  <th scope="col" className="px-[22px] pb-2 pt-1 font-semibold">When</th>
                  <th scope="col" className="px-3 pb-2 pt-1 font-semibold">Action</th>
                  <th scope="col" className="px-3 pb-2 pt-1 font-semibold">Record</th>
                  <th scope="col" className="px-3 pb-2 pt-1 font-semibold">IP address</th>
                  <th scope="col" className="px-[22px] pb-2 pt-1 font-semibold">From</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <Row key={entry.id} entry={entry} />
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between gap-3 border-t border-border px-[22px] py-3">
            <span className="text-[12px] text-muted-foreground">
              {hasMore
                ? `Showing the ${entries.length} most recent.`
                : `Showing all ${entries.length}.`}
            </span>
            {hasMore && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => loadMore()}
                disabled={isLoadingMore}
              >
                {isLoadingMore ? "Loading…" : "Load older"}
              </Button>
            )}
          </div>
        </>
      )}
    </SettingsCard>
  )
}
