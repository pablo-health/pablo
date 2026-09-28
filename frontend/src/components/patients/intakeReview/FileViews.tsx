// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The two questions answered by sending a file: an insurance card and any
 * other document a practice asked for.
 *
 * Each file is named, with the side it shows on a card, and opens through
 * the same document route the chart's file list uses — so opening one here
 * is the same recorded read as opening it there. Nothing is fetched until
 * somebody asks: a review opened to read the answers should not pull every
 * photograph on the form.
 */

import { useState } from "react"

import { sideLabel, sidesOf } from "@/components/portal/forms/renderers/InsuranceCardItem"
import type { IntakeChartArtifact } from "@/lib/api/intakeReview"
import { getPatientDocumentDownloadUrl } from "@/lib/api/patientDocuments"
import type { ItemView, ItemViewProps } from "./types"
import { VIEW_COPY, ViewFrame, questionOf } from "./ViewParts"

function FileRow({ artifact, label }: { artifact: IntakeChartArtifact; label: string | null }) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)

  async function open() {
    setBusy(true)
    setFailed(false)
    try {
      const url = await getPatientDocumentDownloadUrl(artifact.document_id, undefined, "inline")
      window.open(url, "_blank", "noopener,noreferrer")
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1" data-testid={`intake-view-file-${artifact.id}`}>
      {label && <span className="text-sm font-medium text-neutral-700">{label}</span>}
      <span className="min-w-0 truncate text-sm text-neutral-900">{artifact.filename}</span>
      <button type="button" disabled={busy} onClick={() => void open()}
        className="text-xs font-medium text-primary-600 hover:text-primary-700">
        {busy ? VIEW_COPY.opening : VIEW_COPY.open}
      </button>
      {failed && <span role="alert" className="text-xs text-red-700">{VIEW_COPY.openFailed}</span>}
    </li>
  )
}

function FileList({ rows }: { rows: { artifact: IntakeChartArtifact; label: string | null }[] }) {
  if (rows.length === 0) return <p className="text-sm text-neutral-500">{VIEW_COPY.noFiles}</p>
  return (
    <ul className="space-y-1.5">
      {rows.map(({ artifact, label }) => (
        <FileRow key={artifact.id} artifact={artifact} label={label} />
      ))}
    </ul>
  )
}

/** Front first, then back, in the order the card asked for them. */
function InsuranceCardView({ item, artifacts }: ItemViewProps) {
  const order = sidesOf(item.config)
  const rows = [...artifacts]
    .sort((a, b) => order.indexOf(a.side ?? "") - order.indexOf(b.side ?? ""))
    .map((artifact) => ({ artifact, label: artifact.side ? sideLabel(artifact.side) : null }))
  return (
    <ViewFrame heading={questionOf(item)} helpText={item.help_text}>
      <FileList rows={rows} />
    </ViewFrame>
  )
}

function DocumentRequestView({ item, artifacts }: ItemViewProps) {
  return (
    <ViewFrame heading={questionOf(item)} helpText={item.help_text}>
      <FileList rows={artifacts.map((artifact) => ({ artifact, label: null }))} />
    </ViewFrame>
  )
}

export const insuranceCardView: ItemView = { Component: InsuranceCardView, answerable: true }
export const documentRequestView: ItemView = { Component: DocumentRequestView, answerable: true }
