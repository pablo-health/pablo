// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useState } from "react"
import { FileText, ImageOff } from "lucide-react"

import { EligibilityBadge } from "@/components/insurance/EligibilityBadge"
import { usePatientCoverage } from "@/hooks/useCoverage"
import type { IntakeArtifactGroup } from "@/hooks/useIntakeArtifacts"
import type { IntakeChartArtifact } from "@/lib/api/intakeReview"
import { getPatientDocumentDownloadUrl } from "@/lib/api/patientDocuments"

/**
 * Every sentence this section shows, in one block.
 *
 * The scan wording says what was done rather than what is true of the
 * file. "Scanned" is a fact about the scanner; "safe" would be a claim
 * about the contents that nothing here has checked.
 */
const COPY = {
  heading: "Files",
  coverage: "Coverage",
  memberId: (last4: string) => `Member ID ending ${last4}`,
  thumbnailFailed: "Preview unavailable",
  sides: { front: "Front", back: "Back" } as Record<string, string>,
  scan: {
    clean: "Scanned",
    pending: "Scan in progress",
    infected: "Flagged by the scanner",
  } as Record<string, string>,
}

const UNITS = ["bytes", "KB", "MB", "GB"]

/** How big a file is, in the unit a person would say it in. */
export function fileSize(sizeBytes: number): string {
  let size = sizeBytes
  for (const unit of UNITS.slice(0, -1)) {
    if (size < 1024) {
      return unit === "bytes" ? `${sizeBytes} bytes` : `${size.toFixed(1)} ${unit}`
    }
    size /= 1024
  }
  return `${size.toFixed(1)} ${UNITS[UNITS.length - 1]}`
}

/** The last four of a member id, and nothing before them. */
export function maskMemberId(memberId: string): string {
  return memberId.slice(-4)
}

/**
 * A thumbnail for an image, fetched as bytes rather than pointed at.
 *
 * The document route hands back a short-lived signed URL, and the fetch
 * through the authenticated client is what records the read. The bytes
 * become an object URL so nothing in the DOM holds a signed address, and
 * it is revoked when the card goes away — a chart with a dozen files
 * would otherwise keep every one of them alive until the tab closed.
 */
function ArtifactThumbnail({ artifact }: { artifact: IntakeChartArtifact }) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    let created: string | null = null

    getPatientDocumentDownloadUrl(artifact.document_id, undefined, "inline")
      .then((signed) => fetch(signed))
      .then((response) => {
        if (!response.ok) throw new Error(COPY.thumbnailFailed)
        return response.blob()
      })
      .then((blob) => {
        if (cancelled) return
        created = URL.createObjectURL(blob)
        setObjectUrl(created)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })

    return () => {
      cancelled = true
      if (created) URL.revokeObjectURL(created)
    }
  }, [artifact.document_id])

  if (failed) {
    return (
      <div
        data-testid="intake-artifact-thumbnail-failed"
        className="flex h-24 w-24 shrink-0 flex-col items-center justify-center gap-1 rounded border border-border bg-neutral-50 text-neutral-500"
      >
        <ImageOff aria-hidden="true" className="h-5 w-5" />
        <span className="text-[11px]">{COPY.thumbnailFailed}</span>
      </div>
    )
  }

  if (!objectUrl) {
    return (
      <div
        data-testid="intake-artifact-thumbnail-loading"
        className="h-24 w-24 shrink-0 animate-pulse rounded border border-border bg-neutral-100"
      />
    )
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element -- an object URL for bytes already fetched; next/image cannot optimize one
    <img
      data-testid="intake-artifact-thumbnail"
      src={objectUrl}
      alt={artifact.filename}
      className="h-24 w-24 shrink-0 rounded border border-border object-cover"
    />
  )
}

/** A file that is not an image: named, sized, and not drawn. */
function ArtifactChip({ artifact }: { artifact: IntakeChartArtifact }) {
  return (
    <div
      data-testid="intake-artifact-chip"
      className="flex h-24 w-24 shrink-0 flex-col items-center justify-center gap-1 rounded border border-border bg-neutral-50 px-2 text-center text-neutral-600"
    >
      <FileText aria-hidden="true" className="h-5 w-5" />
      <span className="truncate text-[11px] w-full">{artifact.filename}</span>
    </div>
  )
}

/**
 * What a scanner found, when one has looked.
 *
 * Renders nothing at all while `scan_status` is null. No deployment scans
 * yet, so this is dark today — and an "unscanned" chip would be worse
 * than silence, because it would put a state in front of a clinician that
 * nothing is going to move them out of.
 */
function ScanBadge({ status }: { status: string | null }) {
  if (status === null) return null
  return (
    <span
      data-testid="intake-artifact-scan"
      className="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600"
    >
      {COPY.scan[status] ?? status}
    </span>
  )
}

function ArtifactRow({ artifact }: { artifact: IntakeChartArtifact }) {
  const isImage = artifact.content_type.startsWith("image/")
  const side = artifact.side ? COPY.sides[artifact.side] ?? artifact.side : null

  return (
    <div className="flex items-start gap-3" data-testid="intake-artifact">
      {isImage ? <ArtifactThumbnail artifact={artifact} /> : <ArtifactChip artifact={artifact} />}
      <div className="min-w-0 space-y-1">
        <p className="text-sm font-medium text-neutral-900">{artifact.item_label}</p>
        <p className="truncate text-sm text-neutral-700">
          {side ? `${side} · ` : ""}
          {artifact.filename}
        </p>
        <p className="text-xs text-neutral-500">{fileSize(artifact.size_bytes)}</p>
        <ScanBadge status={artifact.scan_status} />
      </div>
    </div>
  )
}

/**
 * The plan on file, as far as the practice has checked it.
 *
 * The member id is shown by its last four. A clinician confirming the
 * card in front of them needs those; the whole number on a screen is one
 * more place it can be read off. The verdict is the same badge the header
 * and the coverage card use, so what a 271 is allowed to claim is decided
 * in one place.
 *
 * Nothing renders when no plan is on file. A client paying out of pocket
 * is the ordinary case, and a box saying so would be on many charts.
 */
export function IntakeCoverageSummary({ patientId }: { patientId: string }) {
  const { data: coverage } = usePatientCoverage(patientId)
  if (!coverage) return null

  return (
    <div data-testid="intake-coverage" className="space-y-1">
      <p className="text-sm font-medium text-neutral-700">{COPY.coverage}</p>
      <p className="text-sm text-neutral-900">{coverage.payer.name}</p>
      <p className="text-sm text-neutral-700">{COPY.memberId(maskMemberId(coverage.member_id))}</p>
      <EligibilityBadge summary={coverage.eligibility} />
    </div>
  )
}

interface IntakeArtifactsProps {
  patientId: string
  groups: IntakeArtifactGroup[]
}

/**
 * What the patient sent in with their forms, one block per form.
 *
 * Renders nothing when no form collected anything, so a chart from before
 * there were file questions is unchanged. The coverage summary sits with
 * the files because the card they photographed is what it was read off.
 */
export function IntakeArtifacts({ patientId, groups }: IntakeArtifactsProps) {
  if (groups.length === 0) return null

  return (
    <div className="mt-4 space-y-4 border-t border-border pt-4" data-testid="intake-artifacts">
      <h3 className="text-sm font-semibold text-neutral-900">{COPY.heading}</h3>

      {groups.map(({ assignment, artifacts }) => (
        <div key={assignment.id} className="space-y-3">
          <p className="text-sm text-neutral-500">
            {assignment.packet_name} v{assignment.version}
          </p>
          {artifacts.map((artifact) => (
            <ArtifactRow key={artifact.id} artifact={artifact} />
          ))}
        </div>
      ))}

      <IntakeCoverageSummary patientId={patientId} />
    </div>
  )
}
