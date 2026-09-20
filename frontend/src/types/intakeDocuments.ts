// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The consent documents a practice writes, as they come off the wire.
 *
 * A row is one VERSION of one document. `document_key` is the document —
 * what a form points at, and what stays the same across every revision;
 * `id` identifies the revision. That distinction is the reason the editor
 * shows a document and the form editor stores a key.
 *
 * `rendered_html` is the server's own rendering of `body_markdown`. The
 * preview shows that rather than rendering the markdown again here, so what
 * a practice proofreads is exactly what a patient will be shown.
 */

/** Who a document asks to sign it. A guardian signs alongside, not instead. */
export const SIGNER_ROLES = ["patient", "guardian"] as const

export type SignerRole = (typeof SIGNER_ROLES)[number]

export interface IntakeDocument {
  id: string
  document_key: string
  title: string
  body_markdown: string
  /** Server-rendered, already safe to insert. See backend app/intake/documents.py. */
  rendered_html: string
  version: number
  /** The sha256 a signature will record. Shown so a practice can match the two. */
  digest: string
  /** Set means frozen. That is the whole of a version's state. */
  published_at: string | null
  requires_signature: boolean
  signer_roles: SignerRole[]
  created_at: string
}

export interface CreateDocumentInput {
  title: string
  body_markdown?: string
  requires_signature?: boolean
  signer_roles?: SignerRole[]
}

export interface UpdateDocumentInput {
  title?: string
  body_markdown?: string
}
