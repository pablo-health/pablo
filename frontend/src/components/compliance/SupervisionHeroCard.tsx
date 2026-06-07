// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  useSupervisionRelationships,
  useSupervisionHours,
} from "@/hooks/useSupervision"
import type {
  SupervisionRelationship,
  RelationshipStatus,
} from "@/types/supervision"
import { daysUntil, formatDueLabel } from "./urgency"
import { horizonFor } from "./horizons"

/**
 * Provider cockpit — supervision and delegation hero card.
 *
 * Surfaces the clinician's active supervision relationships at the top of
 * the compliance view. Shows supervisor details, next-review countdown,
 * and (for clinical_supervision) an expandable accrued-hours summary.
 *
 * Rendered above the item list in CompliancePanel when the user has at
 * least one relationship configured. Empty if the backend returns none
 * (no call made for users with no supervision relationships on record).
 */
export function SupervisionHeroCard() {
  const { data: relationships = [], isLoading } =
    useSupervisionRelationships()

  if (isLoading || relationships.length === 0) return null

  return (
    <div className="mb-4 space-y-2">
      {relationships.map((rel) => (
        <RelationshipCard key={rel.id} relationship={rel} />
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Single relationship card
// ---------------------------------------------------------------------------

function RelationshipCard({
  relationship: rel,
}: {
  relationship: SupervisionRelationship
}) {
  const isClinical = rel.relationship_type === "clinical_supervision"
  const [hoursOpen, setHoursOpen] = useState(false)

  const days = daysUntil(rel.next_review_date)
  const horizon = horizonFor(days)
  const reviewBadge = reviewBadgeFor(horizon)

  const statusBadge = statusBadgeFor(rel.status)
  const typeLabel = relationshipTypeLabel(rel.relationship_type)

  return (
    <div
      className="rounded-xl border border-neutral-200 bg-white shadow-sm overflow-hidden"
      data-testid="supervision-hero-card"
    >
      {/* Header strip */}
      <div className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-primary-50/60 to-transparent border-b border-neutral-100">
        <span
          className={`inline-flex items-center text-[10.5px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide ${statusBadge}`}
        >
          {rel.status}
        </span>
        <span className="text-xs text-neutral-500">{typeLabel}</span>
      </div>

      {/* Body */}
      <div className="px-4 py-3 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 items-start">
        {/* Supervisor block */}
        <div>
          <p className="text-sm font-semibold text-neutral-900">
            {rel.supervisor_name}
          </p>
          {rel.supervisor_credential && (
            <p className="text-xs text-neutral-500 mt-0.5">
              {rel.supervisor_credential}
            </p>
          )}
          {rel.state && (
            <p className="text-xs text-neutral-400 mt-0.5">{rel.state}</p>
          )}
        </div>

        {/* Next review countdown */}
        {rel.next_review_date && (
          <div className="text-right shrink-0">
            <span
              className={`inline-flex items-center text-[11px] font-medium px-2 py-0.5 rounded-full ${reviewBadge}`}
            >
              {formatDueLabel(days)}
            </span>
            <p className="text-[10px] text-neutral-400 mt-0.5">
              {formatDate(rel.next_review_date)}
            </p>
          </div>
        )}

        {/* DEA numbers */}
        {(rel.supervisor_dea ?? rel.supervisor_license) && (
          <div className="col-span-2 mt-1 flex flex-wrap gap-3">
            {rel.supervisor_dea && (
              <Pill label="Supervisor DEA" value={rel.supervisor_dea} />
            )}
            {rel.supervisor_license && (
              <Pill label="License" value={rel.supervisor_license} />
            )}
          </div>
        )}

        {/* Authority reference */}
        {rel.authority_ref && (
          <p className="col-span-2 text-[10.5px] text-neutral-400 mt-1">
            Authority: {rel.authority_ref}
          </p>
        )}
      </div>

      {/* Hours accordion (clinical supervision only) */}
      {isClinical && (
        <div className="border-t border-neutral-100">
          <button
            type="button"
            onClick={() => setHoursOpen((o) => !o)}
            aria-expanded={hoursOpen}
            className="w-full flex items-center justify-between px-4 py-2 text-xs font-medium text-neutral-600 hover:bg-neutral-50 transition-colors"
          >
            <span>Accrued hours</span>
            <span
              aria-hidden
              className={`transition-transform duration-200 ${hoursOpen ? "rotate-180" : ""}`}
            >
              ▾
            </span>
          </button>
          {hoursOpen && <HoursPanel relationshipId={rel.id} />}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Hours panel (lazy — only fetched when accordion opens)
// ---------------------------------------------------------------------------

function HoursPanel({ relationshipId }: { relationshipId: string }) {
  const { data: entries = [], isLoading } =
    useSupervisionHours(relationshipId)

  if (isLoading) {
    return (
      <p className="px-4 py-3 text-xs text-neutral-400">Loading hours…</p>
    )
  }

  if (entries.length === 0) {
    return (
      <p className="px-4 py-3 text-xs text-neutral-400">
        No hours logged yet.
      </p>
    )
  }

  const total = entries.reduce((sum, e) => sum + e.hours, 0)

  return (
    <div className="px-4 pb-3 space-y-1">
      <p className="text-xs font-semibold text-neutral-700 mb-2">
        {total.toFixed(1)} hrs total
      </p>
      <ul className="space-y-1" role="list" aria-label="Accrued supervision hours">
        {entries.map((entry) => (
          <li
            key={entry.id}
            className="flex items-center justify-between gap-2 rounded-md border border-neutral-100 bg-neutral-50 px-3 py-1.5"
          >
            <div className="min-w-0">
              <span className="text-[11px] font-medium text-neutral-700 capitalize">
                {entry.kind.replace(/_/g, " ")}
              </span>
              {entry.notes && (
                <span className="ml-1.5 text-[10.5px] text-neutral-400 truncate">
                  — {entry.notes}
                </span>
              )}
            </div>
            <div className="text-right shrink-0">
              <span className="text-[11px] font-semibold text-neutral-800">
                {entry.hours.toFixed(1)} hrs
              </span>
              <p className="text-[10px] text-neutral-400">
                {formatDate(entry.logged_date)}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function Pill({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-1">
      <span className="text-[10px] uppercase tracking-wide text-neutral-400 font-medium">
        {label}
      </span>
      <span className="text-[11px] font-mono text-neutral-700">{value}</span>
    </div>
  )
}

function formatDate(iso: string): string {
  try {
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    }).format(new Date(iso))
  } catch {
    return iso
  }
}

function reviewBadgeFor(
  horizon: ReturnType<typeof horizonFor>,
): string {
  switch (horizon) {
    case "overdue":
      return "bg-rose-100 text-rose-800"
    case "week":
      return "bg-amber-100 text-amber-800"
    case "month":
      return "bg-amber-50 text-amber-700"
    case "quarter":
      return "bg-emerald-50 text-emerald-700"
    default:
      return "bg-neutral-100 text-neutral-600"
  }
}

function statusBadgeFor(status: RelationshipStatus): string {
  switch (status) {
    case "active":
      return "bg-emerald-100 text-emerald-800"
    case "lapsed":
      return "bg-rose-100 text-rose-800"
    case "pending":
      return "bg-amber-100 text-amber-800"
  }
}

function relationshipTypeLabel(type: string): string {
  return type
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
}
