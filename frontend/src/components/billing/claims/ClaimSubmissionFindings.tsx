// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What the clearinghouse or the payer said was wrong with a filed claim.
 *
 * Kept apart from the scrub's own findings, and never merged with them: one
 * list is what this practice checked before filing and the other is what the
 * vendor answered, and a person deciding what to do next has to be able to
 * tell which is which. The vendor's wording can quote the field at fault, so
 * this renders in the claim's own audited detail view and nowhere else — not
 * in a tracker row, not in a queue row, not in a toast.
 */

"use client"

import { AlertTriangle } from "lucide-react"
import type { SubmissionFinding } from "@/types/claims"

const SOURCE_LABELS: Record<SubmissionFinding["source"], string> = {
  edit: "Clearinghouse edit",
  status: "Claim status",
}

export function ClaimSubmissionFindings({ findings }: { findings: SubmissionFinding[] }) {
  if (findings.length === 0) return null

  return (
    <div
      className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm"
      data-testid="claim-submission-findings"
    >
      <p className="flex items-center gap-2 font-medium text-red-900">
        <AlertTriangle className="h-4 w-4" aria-hidden />
        From the clearinghouse / payer
      </p>
      <ul className="mt-2 ml-6 list-disc space-y-2 text-red-800">
        {findings.map((finding) => (
          <li key={`${finding.source}-${finding.code}`}>
            <span className="font-mono text-xs">
              {SOURCE_LABELS[finding.source]} {finding.code}
            </span>
            <p>{finding.description}</p>
            {finding.followup_action && (
              <p className="text-red-700">{finding.followup_action}</p>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
