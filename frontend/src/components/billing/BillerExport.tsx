// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * "Export for biller" — the claim-ready CSV for a date range.
 *
 * A practice that files through a biller hands over one CSV row per
 * service line of every validated claim dated in the range. Drafts never
 * leave; a claim that would leave with a blocking finding stops the whole
 * export, and the refusal lists each one so the clinician can fix it and
 * try again. Nothing here changes a claim.
 */

"use client"

import { useState } from "react"
import { Download } from "lucide-react"
import { blockedClaimsFrom, downloadClaimsCsv } from "@/lib/api/claims"
import type { ClaimExportFinding } from "@/types/claims"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10)
}

function firstOfMonth(date: Date): string {
  return isoDate(new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1)))
}

export function BillerExport() {
  const today = new Date()
  const [from, setFrom] = useState(firstOfMonth(today))
  const [to, setTo] = useState(isoDate(today))
  const [exporting, setExporting] = useState(false)
  const [blocked, setBlocked] = useState<ClaimExportFinding[] | null>(null)
  const [failed, setFailed] = useState(false)

  const rangeInvalid = !from || !to || to < from

  async function handleExport() {
    setExporting(true)
    setBlocked(null)
    setFailed(false)
    try {
      const blob = await downloadClaimsCsv(from, to)
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `claims-${from}-${to}.csv`
      a.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      const refused = blockedClaimsFrom(error)
      if (refused) {
        setBlocked(refused)
      } else {
        setFailed(true)
      }
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="card space-y-4">
      <div>
        <h2 className="text-lg font-display font-semibold text-neutral-900">Export for biller</h2>
        <p className="mt-1 text-sm text-neutral-600">
          A claim-ready CSV, one row per service line, for every validated claim with a visit in
          the range. Drafts stay here until they pass validation.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="biller-export-from">From</Label>
          <Input
            id="biller-export-from"
            type="date"
            value={from}
            max={to}
            onChange={(e) => setFrom(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="biller-export-to">To</Label>
          <Input
            id="biller-export-to"
            type="date"
            value={to}
            min={from}
            onChange={(e) => setTo(e.target.value)}
          />
        </div>
        <Button onClick={handleExport} disabled={exporting || rangeInvalid}>
          <Download aria-hidden />
          {exporting ? "Exporting…" : "Export for biller"}
        </Button>
      </div>

      {failed && (
        <p role="alert" className="text-sm text-red-700">
          The export could not be prepared. Try again in a moment.
        </p>
      )}

      {blocked && <BlockedClaims claims={blocked} />}
    </div>
  )
}

function BlockedClaims({ claims }: { claims: ClaimExportFinding[] }) {
  return (
    <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
      <p className="font-medium text-amber-900">
        Nothing was exported. {claims.length === 1 ? "One claim needs" : `${claims.length} claims need`}{" "}
        attention first:
      </p>
      <ul className="mt-2 space-y-2">
        {claims.map((claim) => (
          <li key={claim.claim_id}>
            <p className="font-mono text-xs text-amber-900">Claim {claim.control_number}</p>
            <ul className="ml-4 list-disc text-amber-800">
              {claim.findings.map((finding) => (
                <li key={`${finding.code}-${finding.field ?? ""}`}>{finding.message}</li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  )
}
