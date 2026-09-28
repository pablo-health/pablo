// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What the chart hands the portal's renderers to draw a form read-only.
 *
 * The renderers are the patient's, and the two that write for themselves
 * read their state through a patient session the chart does not have. This
 * is the adapter: the same facts, read the chart's way — signatures from the
 * review, a consent document from the practice's own document route, and
 * files named from the chart's file list and opened through the document
 * route the rest of the chart uses, so opening one here is the same recorded
 * read as opening it there.
 *
 * Which questions the patient was shown is decided by the same visibility
 * rules the portal walk runs, over the answers the review carries. A
 * question a rule hid was never asked, and the chart says so rather than
 * drawing it as unanswered — the same distinction the exported copy draws.
 */

import { measureSize } from "@/components/portal/forms/PacketFlow"
import type { ReadOnlySource } from "@/components/portal/forms/renderers/types"
import { getIntakeDocument } from "@/lib/api/intakeDocuments"
import type { IntakeChartArtifact, IntakeReview, IntakeReviewItem } from "@/lib/api/intakeReview"
import type { IntakeArtifact } from "@/lib/api/patientIntake"
import { getPatientDocumentDownloadUrl } from "@/lib/api/patientDocuments"
import { evaluate, ruleOf } from "@/lib/intake/visibility"

export function readOnlySource(review: IntakeReview, files: IntakeChartArtifact[]): ReadOnlySource {
  return {
    signatures: review.signatures,
    loadDocument: async (versionId) => {
      const document = await getIntakeDocument(versionId)
      return { title: document.title, rendered_html: document.rendered_html }
    },
    filenames: Object.fromEntries(files.map((file) => [file.id, file.filename])),
    openFile: async (documentId) => {
      const url = await getPatientDocumentDownloadUrl(documentId, undefined, "inline")
      window.open(url, "_blank", "noopener,noreferrer")
    },
  }
}

/** The chart's file rows in the shape the renderers read, for one question. */
export function artifactsFor(
  itemId: string,
  assignmentId: string,
  files: IntakeChartArtifact[],
): IntakeArtifact[] {
  return files
    .filter((file) => file.item_id === itemId)
    .map((file) => ({
      id: file.id,
      assignment_id: assignmentId,
      item_id: file.item_id,
      document_id: file.document_id,
      side: file.side,
      created_at: file.created_at,
    }))
}

/** Which questions the patient was shown, by item key. */
export function shownKeys(review: IntakeReview, items: IntakeReviewItem[]): Record<string, boolean> {
  return evaluate(
    items.map((item) => ({
      key: item.key,
      rule: ruleOf(item.config),
      instrumentItems: measureSize(item, review.form),
    })),
    Object.fromEntries(items.map((item) => [item.key, item.value])),
  )
}
