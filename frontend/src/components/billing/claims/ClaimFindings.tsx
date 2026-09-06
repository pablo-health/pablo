// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What the scrub found on a claim. Blocking findings stop filing and read
 * as such; warnings are worth a look and stop nothing.
 */

"use client"

import { AlertTriangle, CheckCircle2, Info } from "lucide-react"
import type { ClaimFinding } from "@/types/claims"

interface ClaimFindingsProps {
  findings: ClaimFinding[]
  /** Copy for the empty case; omit to render nothing when there are no findings. */
  emptyText?: string
}

export function ClaimFindings({ findings, emptyText }: ClaimFindingsProps) {
  const blocking = findings.filter((f) => f.severity === "blocking")
  const warnings = findings.filter((f) => f.severity === "warning")

  if (findings.length === 0) {
    if (!emptyText) return null
    return (
      <p className="flex items-center gap-2 text-sm text-emerald-800">
        <CheckCircle2 className="h-4 w-4" aria-hidden />
        {emptyText}
      </p>
    )
  }

  return (
    <div className="space-y-3" data-testid="claim-findings">
      {blocking.length > 0 && (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm">
          <p className="flex items-center gap-2 font-medium text-red-900">
            <AlertTriangle className="h-4 w-4" aria-hidden />
            {blocking.length === 1
              ? "One thing stops this claim from being filed:"
              : `${blocking.length} things stop this claim from being filed:`}
          </p>
          <FindingList findings={blocking} className="text-red-800" />
        </div>
      )}
      {warnings.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
          <p className="flex items-center gap-2 font-medium text-amber-900">
            <Info className="h-4 w-4" aria-hidden />
            Worth a look before filing:
          </p>
          <FindingList findings={warnings} className="text-amber-800" />
        </div>
      )}
    </div>
  )
}

function FindingList({ findings, className }: { findings: ClaimFinding[]; className: string }) {
  return (
    <ul className={`mt-2 ml-6 list-disc space-y-1 ${className}`}>
      {findings.map((finding) => (
        <li key={`${finding.code}-${finding.field ?? ""}`}>{finding.message}</li>
      ))}
    </ul>
  )
}
