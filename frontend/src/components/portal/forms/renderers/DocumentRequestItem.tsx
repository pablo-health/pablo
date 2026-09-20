// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Any other file a practice asked for.
 *
 * A referral letter, records from a previous provider, a form filled in on
 * paper. Unlike a card there is no fixed number of them, so the screen
 * shows what has arrived and one empty slot underneath — send as many as
 * you have.
 *
 * **A practice that works from paper can offer the form first.** When the
 * question names one of the practice's own blank forms, the screen leads
 * with a download and says what to do with it: download, fill it in,
 * photograph or scan it, send it back. That is the fallback rather than the
 * path, which is why it is a line above the upload rather than the shape of
 * the whole screen — most of what this question asks for is something the
 * client already has.
 *
 * **It writes for itself, like a card.** The question's answer names the
 * documents that arrived, so Continue saves nothing here and what is on
 * screen after an upload is the server's answer, re-read.
 */

import { useState } from "react"
import { blankFormUrl, PatientIntakeError } from "@/lib/api/patientIntake"
import { BLANK_FORM_DOWNLOAD, BLANK_FORM_NOTE, filesSent } from "../formsCopy"
import { QuestionFrame, labelOf } from "./QuestionFrame"
import { acceptOf, UploadSlot } from "./UploadSlot"
import type { AnswerValue, ItemRenderer, ItemRendererProps } from "./types"

/** The practice's own form this question offers, when it names one. */
function blankFormIdOf(config: Record<string, unknown>): string | null {
  const id = config.blank_form_id
  return typeof id === "string" && id !== "" ? id : null
}

function DocumentRequestItem({
  item,
  artifacts,
  assignmentId,
  sessionToken,
  onWrote,
  onSessionLost,
}: ItemRendererProps) {
  const accept = acceptOf(item.config)
  const blankFormId = blankFormIdOf(item.config)

  return (
    <QuestionFrame item={item}>
      {() => (
        <div className="space-y-4">
          {blankFormId !== null && (
            <BlankForm
              blankFormId={blankFormId}
              sessionToken={sessionToken}
              onSessionLost={onSessionLost}
            />
          )}

          <div data-testid="forms-document-request" className="space-y-3">
            {artifacts.map((artifact) => (
              <UploadSlot
                key={artifact.id}
                assignmentId={assignmentId}
                sessionToken={sessionToken}
                itemId={item.id}
                label={null}
                artifact={artifact}
                accept={accept}
                onWrote={onWrote}
                onSessionLost={onSessionLost}
              />
            ))}
            {/* One empty slot, always: there is no fixed number of these. */}
            <UploadSlot
              assignmentId={assignmentId}
              sessionToken={sessionToken}
              itemId={item.id}
              label={null}
              artifact={null}
              accept={accept}
              onWrote={onWrote}
              onSessionLost={onSessionLost}
            />
          </div>
        </div>
      )}
    </QuestionFrame>
  )
}

/**
 * The practice's blank form, offered for download.
 *
 * The URL is fetched on the click rather than up front: it is short-lived,
 * and minting one for a screen somebody may never act on is a disclosure
 * nobody asked for. A form the practice has since stopped using answers
 * 404, and the line simply does not open anything — which is the truthful
 * thing for it to do.
 */
function BlankForm({
  blankFormId,
  sessionToken,
  onSessionLost,
}: {
  blankFormId: string
  sessionToken: string
  onSessionLost: () => void
}) {
  const [busy, setBusy] = useState(false)

  async function open() {
    setBusy(true)
    try {
      const url = await blankFormUrl(sessionToken, blankFormId)
      window.open(url, "_blank", "noopener,noreferrer")
    } catch (raised: unknown) {
      if (raised instanceof PatientIntakeError && raised.kind === "expired") onSessionLost()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div data-testid="forms-blank-form" className="rounded-md border border-neutral-200 p-3">
      <button
        type="button"
        data-testid="forms-blank-form-download"
        className="text-sm font-medium text-primary-700 underline"
        disabled={busy}
        onClick={() => void open()}
      >
        {BLANK_FORM_DOWNLOAD}
      </button>
      <p className="mt-1 text-sm text-neutral-600">{BLANK_FORM_NOTE}</p>
    </div>
  )
}

/** The review row: how many files arrived, never what they were called. */
function summaryOf(value: AnswerValue | null): string | null {
  const documents = value?.documents
  if (!Array.isArray(documents) || documents.length === 0) return null
  return filesSent(documents.length)
}

export const documentRequestRenderer: ItemRenderer = {
  Component: DocumentRequestItem,
  // The walk's save route refuses this type outright: its answer names the
  // documents that arrived, and only the route that records an arrival may
  // write one.
  answerable: false,
  // But it is a question, not a heading — so it is counted and reviewed.
  writesItself: true,
  label: labelOf,
  summary: summaryOf,
}
