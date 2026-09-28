// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A consent document drawn back after the fact, and the signatures on it.
 *
 * The read-only half of `ConsentDocumentItem`, in its own file because the
 * two share a heading and a signed row and nothing else: the signing half
 * reads through the patient's session and writes, this half reads what the
 * caller hands it and never calls a route of its own.
 *
 * Each signature shows the statement it was taken under as the signature
 * row recorded it, rather than today's wording. A signature is evidence of
 * what was agreed then, and a copy of the sentence from anywhere else would
 * be free to say something different.
 */

import { useQuery } from "@tanstack/react-query"
import type { IntakeSignature } from "@/lib/api/patientIntake"
import {
  CONSENT_LOAD_FAILED,
  CONSENT_LOADING,
  CONSENT_NOT_SIGNED,
  CONSENT_SIGNED_BADGE,
  consentSignedBy,
  consentSignerRole,
} from "../formsCopy"
import type { ItemRendererProps, ReadOnlySource } from "./types"

/** A signature's timestamp, in the reader's own locale. */
export function signedAtLabel(signedAt: string): string {
  const when = new Date(signedAt)
  return Number.isNaN(when.getTime()) ? signedAt : when.toLocaleString()
}

export function SignedRow({ signature }: { signature: IntakeSignature }) {
  return (
    <li className="flex flex-wrap items-center gap-2 text-sm text-neutral-800">
      <span className="rounded-full bg-primary-50 px-2 py-0.5 text-xs font-medium text-primary-800">
        {CONSENT_SIGNED_BADGE}
      </span>
      <span>{consentSignedBy(signature.signer_typed_name, signedAtLabel(signature.signed_at))}</span>
    </li>
  )
}

export function ConsentDocumentRead({
  item,
  pinned,
  readOnly,
}: {
  item: ItemRendererProps["item"]
  pinned: string
  readOnly: ReadOnlySource
}) {
  const document = useQuery({
    queryKey: ["intake-read", "document", pinned],
    queryFn: () => readOnly.loadDocument(pinned),
    retry: false,
  })
  const mine = readOnly.signatures.filter((row) => row.item_id === item.id)
  const heading = item.label?.trim() || document.data?.title || ""

  return (
    <section data-testid="forms-consent" aria-labelledby={`forms-consent-${item.id}`}>
      <h2 id={`forms-consent-${item.id}`} className="text-lg font-semibold text-neutral-900">
        {heading}
      </h2>
      {item.help_text?.trim() && (
        <p data-testid="forms-question-help" className="mt-2 text-sm text-neutral-600">
          {item.help_text.trim()}
        </p>
      )}

      {document.isError ? (
        <p className="mt-4 text-sm text-neutral-600">{CONSENT_LOAD_FAILED}</p>
      ) : document.isPending ? (
        <p className="mt-4 text-sm text-neutral-600">{CONSENT_LOADING}</p>
      ) : (
        <div
          data-testid="forms-consent-document"
          className="prose-sm mt-4 max-h-80 overflow-y-auto rounded-md border border-neutral-200 p-4 text-sm leading-relaxed text-neutral-800 print:max-h-none print:overflow-visible"
          // Built server-side by escaping the practice's text first and
          // emitting a fixed set of tags second, the same HTML the signing
          // screen sets. See `backend/app/intake/documents.py`.
          dangerouslySetInnerHTML={{ __html: document.data.rendered_html }}
        />
      )}

      {mine.length === 0 ? (
        <p data-testid="forms-consent-unsigned" className="mt-4 text-sm text-neutral-600">
          {CONSENT_NOT_SIGNED}
        </p>
      ) : (
        <ul data-testid="forms-consent-signed" className="mt-4 flex flex-col gap-3">
          {mine.map((signature) => (
            <li key={signature.id} className="space-y-1.5">
              <label className="flex items-start gap-2 text-sm text-neutral-800">
                <input type="checkbox" className="mt-0.5 h-4 w-4" checked disabled readOnly />
                <span>{signature.consent_statement}</span>
              </label>
              <ul>
                <SignedRow signature={signature} />
              </ul>
              <p data-testid="forms-consent-signer-role" className="text-xs text-neutral-600">
                {consentSignerRole(signature.signer_role)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
