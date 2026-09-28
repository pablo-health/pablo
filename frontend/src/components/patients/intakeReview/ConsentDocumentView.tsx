// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A consent document, and who signed it.
 *
 * Each signature reads back the way it was given: the statement that was
 * ticked, in the wording the signature row recorded, and the name that was
 * typed against it. That wording comes from the row rather than from today's
 * copy, because a signature is evidence of what was agreed then.
 *
 * The document itself is the version the form pinned, fetched from the
 * practice's own document route when somebody asks for it. It is the
 * practice's paperwork rather than anything about this patient, and a long
 * document opened by default would bury the rest of the form.
 */

import { useState } from "react"

import { pinnedVersionOf } from "@/components/portal/forms/renderers/ConsentDocumentItem"
import { useAuthQuery } from "@/hooks/useAuthQuery"
import { getIntakeDocument } from "@/lib/api/intakeDocuments"
import type { IntakeReviewSignature } from "@/lib/api/intakeReview"
import type { IntakeDocument } from "@/types/intakeDocuments"
import type { ItemView, ItemViewProps } from "./types"
import { NoAnswer, VIEW_COPY, ViewFrame } from "./ViewParts"

function formatMoment(iso: string): string {
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString()
}

function DocumentText({ versionId }: { versionId: string }) {
  const { data, isError, isPending } = useAuthQuery<IntakeDocument>({
    queryKey: ["intakeDocument", versionId],
    queryFn: () => getIntakeDocument(versionId),
    retry: false,
  })
  if (isError) return <p className="text-sm text-neutral-500">{VIEW_COPY.documentFailed}</p>
  if (isPending) return <div className="h-16 animate-pulse rounded-md bg-neutral-100" />
  return (
    <div
      data-testid="intake-view-consent-document"
      className="prose-sm max-h-60 overflow-y-auto rounded-md border border-neutral-200 p-3 text-sm leading-relaxed text-neutral-800"
      // Built server-side by escaping the practice's text first and emitting
      // a fixed set of tags second — the same HTML the portal renders. See
      // `backend/app/intake/documents.py`.
      dangerouslySetInnerHTML={{ __html: data.rendered_html }}
    />
  )
}

function SignatureRow({ signature }: { signature: IntakeReviewSignature }) {
  return (
    <li className="space-y-1" data-testid={`intake-view-signature-${signature.id}`}>
      <label className="flex items-start gap-2 text-sm text-neutral-800">
        <input type="checkbox" checked disabled readOnly className="mt-0.5 h-4 w-4" />
        <span>{signature.consent_statement}</span>
      </label>
      <p className="text-sm text-neutral-900">
        <span className="mr-2 rounded-full bg-primary-50 px-2 py-0.5 text-xs font-medium text-primary-800">
          {signature.signer_typed_name}
        </span>
        {VIEW_COPY.signedAs(signature.signer_role)} · {formatMoment(signature.signed_at)}
      </p>
    </li>
  )
}

function ConsentDocumentView({ item, signatures }: ItemViewProps) {
  const [open, setOpen] = useState(false)
  const pinned = pinnedVersionOf(item.config)
  const heading = item.label?.trim() || item.key

  return (
    <ViewFrame heading={heading} helpText={item.help_text}>
      {pinned !== null && (
        <div className="mb-2">
          <button type="button" aria-expanded={open} onClick={() => setOpen((shown) => !shown)}
            className="text-xs font-medium text-primary-600 hover:text-primary-700"
            data-testid={`intake-view-consent-toggle-${item.id}`}>
            {open ? VIEW_COPY.hideDocument : VIEW_COPY.showDocument}
          </button>
          {open && <div className="mt-2"><DocumentText versionId={pinned} /></div>}
        </div>
      )}
      {signatures.length === 0 ? (
        <NoAnswer />
      ) : (
        <ul className="space-y-3">
          {signatures.map((signature) => (
            <SignatureRow key={signature.id} signature={signature} />
          ))}
        </ul>
      )}
    </ViewFrame>
  )
}

export const consentDocumentView: ItemView = { Component: ConsentDocumentView, answerable: true }
