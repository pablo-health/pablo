// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The refills tile's line on the portal home screen: where the most recent
 * request stands, and since when — "Sent to your pharmacy · Sep 28".
 *
 * The status words are the refills section's own labels, so the tile and the
 * list never describe one request two ways. The date is when that status was
 * reached: the prescriber's decision for an answered request, the asking for
 * one still waiting.
 *
 * Reads the request list under the refills section's query key, so opening
 * the section draws from what the tile just fetched. The key is spelled out
 * here rather than imported, keeping this file out of the section's
 * internals; `__tests__/RefillsSummary.test.tsx` renders both against one
 * cache and fails if they ever stop sharing it.
 *
 * Renders nothing while loading or on a failed fetch.
 */

"use client"

import { useQuery } from "@tanstack/react-query"
import { listRefillRequests, type RefillRequest } from "@/lib/api/patientRefills"
import { STATUS_LABELS } from "./refillsCopy"

export const requestsKey = (token: string) => ["patient-refills", "requests", token] as const

function shortDate(iso: string): string | null {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return null
  return at.toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

export function refillsSummaryLine(requests: RefillRequest[]): string {
  const latest = [...requests].sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
  if (latest === undefined) return "No refill requests"
  const label = STATUS_LABELS[latest.status]
  const date = shortDate(latest.decided_at ?? latest.created_at)
  return date === null ? label : `${label} · ${date}`
}

export function RefillsSummary({ sessionToken }: { sessionToken: string }) {
  const requests = useQuery({
    queryKey: requestsKey(sessionToken),
    queryFn: () => listRefillRequests(sessionToken),
  })
  if (!requests.data) return null
  return <>{refillsSummaryLine(requests.data.data)}</>
}
