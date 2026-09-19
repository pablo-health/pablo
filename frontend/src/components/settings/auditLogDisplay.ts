// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Display helpers for the audit trail.
 *
 * Everything here is a pure transform of what the API already returned. In
 * particular, nothing resolves an id to a name: the audit log is documented
 * as PHI-free, and looking up the patient behind `patient_id` to make the
 * row friendlier would quietly turn this screen into a second view of the
 * chart, with a different data classification than the one it claims.
 */

import type { AuditLogItem } from "@/lib/api/users"

/** Words that read wrong in sentence case, because they are not words. */
const ACRONYMS: Record<string, string> = {
  soap: "SOAP",
  baa: "BAA",
  mfa: "MFA",
  otp: "OTP",
  phi: "PHI",
  sms: "SMS",
  ip: "IP",
  npi: "NPI",
  ical: "iCal",
  ehr: "EHR",
  pdf: "PDF",
  csv: "CSV",
  id: "ID",
  url: "URL",
  api: "API",
}

/**
 * `patient_viewed` → `Patient viewed`.
 *
 * Generic on purpose. A hand-written label per action would be a second
 * list to keep in step with the server's, and the day it drifts the log
 * starts describing events by the wrong name — worse than a plain one.
 */
export function formatAuditAction(action: string): string {
  const words = action.split("_").filter(Boolean)
  if (words.length === 0) return action
  const spelled = words.map((word) => ACRONYMS[word] ?? word)
  const [first, ...rest] = spelled
  const head = ACRONYMS[words[0]] ? first : first.charAt(0).toUpperCase() + first.slice(1)
  return [head, ...rest].join(" ")
}

/** `patient` → `Patient`. Same reasoning as above. */
export function formatResourceType(resourceType: string): string {
  return formatAuditAction(resourceType)
}

/**
 * The absolute local time, spelled out.
 *
 * Not "2 hours ago": someone reading this is usually checking whether a
 * specific access was theirs, and a relative time cannot answer that.
 */
export function formatAuditTimestamp(timestamp: string): string {
  const parsed = new Date(timestamp)
  if (Number.isNaN(parsed.getTime())) return timestamp
  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  })
}

/**
 * A short name for the browser or app behind the request.
 *
 * The full string stays available on hover — it is the part that lets you
 * recognise access you do not remember making, so it is never discarded,
 * only shortened.
 */
export function summarizeUserAgent(userAgent: string | null): string {
  if (!userAgent) return "—"
  const known: Array<[RegExp, string]> = [
    [/Pablo[- ]?Companion/i, "Pablo Companion"],
    [/Edg\//, "Edge"],
    [/OPR\/|Opera/, "Opera"],
    [/Firefox\//, "Firefox"],
    [/Chrome\//, "Chrome"],
    [/Safari\//, "Safari"],
    [/curl\//i, "curl"],
    [/python-requests/i, "python-requests"],
  ]
  for (const [pattern, label] of known) {
    if (pattern.test(userAgent)) return label
  }
  return userAgent.length > 40 ? `${userAgent.slice(0, 40)}…` : userAgent
}

/**
 * Which id this row is actually about.
 *
 * `resource_id` is polymorphic — it holds whichever id the action was for —
 * so the row shows that one, and says which kind it is. Ids, never names.
 */
export function describeAuditResource(entry: AuditLogItem): string {
  return `${formatResourceType(entry.resource_type)} ${entry.resource_id}`
}

/**
 * Whether the row was written by someone other than the account holder.
 *
 * A trail scoped to you is not the same as a trail written by you: a public
 * booking lands in your practice's log as an anonymous actor, and that
 * distinction is the whole reason `actor_type` travels with the row.
 */
export function isSomeoneElsesAction(entry: AuditLogItem): boolean {
  return entry.actor_type !== "clinician"
}
