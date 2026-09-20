// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Reading a consent document, and typing a name against it.
 *
 * The one renderer that writes for itself. Everything else on this walk
 * collects a value and lets Continue save it; signing is its own route with
 * its own refusals — already signed, a newer version to read first, a
 * document the practice has not finished setting up — and none of those fit
 * through the save path.
 *
 * **The words come from the server, and so does the sentence under them.**
 * The document is rendered server-side from the markdown a practice typed,
 * by escaping first and emitting a fixed set of tags second, so nothing a
 * practice could type reaches the browser as markup. The consent statement
 * is served too, because the version of it is recorded on the signature —
 * a copy in this file would be free to drift from what the signature says
 * was agreed.
 *
 * **What is on screen after a signature is the server's answer.** The list
 * of signatures is read back rather than remembered here, so a form signed
 * yesterday on another device still looks signed today.
 *
 * **Nothing here decides whether the form is finished.** Sign is offered
 * when the box is ticked and a name is typed; everything else is the
 * server's to refuse, and what it says is shown beside the button.
 */

import { useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  fetchConsentDocument,
  listSignatures,
  PatientIntakeError,
  signConsentDocument,
  type IntakeSignature,
} from "@/lib/api/patientIntake"
import {
  CONSENT_AWAITING_GUARDIAN,
  CONSENT_AWAITING_PATIENT,
  CONSENT_GUARDIAN_NOTE,
  CONSENT_LOAD_FAILED,
  CONSENT_LOADING,
  CONSENT_NAME_LABEL,
  CONSENT_NEEDS_RESIGN,
  CONSENT_REVIEW_LABEL,
  CONSENT_ROLE_GUARDIAN,
  CONSENT_ROLE_LABEL,
  CONSENT_ROLE_PATIENT,
  CONSENT_SIGN,
  CONSENT_SIGNED_BADGE,
  CONSENT_SIGN_FAILED,
  CONSENT_SIGNING,
  consentSignedBy,
} from "../formsCopy"
import { Unavailable } from "./DisplayItem"
import type { AnswerValue, ItemRenderer, ItemRendererProps } from "./types"

const NAME_MAX = 160

/** What the practice pinned when it published the form. */
function pinnedVersionOf(config: Record<string, unknown>): string | null {
  const pinned = config.document_version_id
  return typeof pinned === "string" && pinned !== "" ? pinned : null
}

function roleLabel(role: string): string {
  return role === "guardian" ? CONSENT_ROLE_GUARDIAN : CONSENT_ROLE_PATIENT
}

/** A signature's timestamp, in the reader's own locale. */
function signedAtLabel(signedAt: string): string {
  const when = new Date(signedAt)
  return Number.isNaN(when.getTime()) ? signedAt : when.toLocaleString()
}

function ConsentDocumentItem({
  item,
  assignmentId,
  sessionToken,
  onWrote,
  onSessionLost,
}: ItemRendererProps) {
  const pinned = pinnedVersionOf(item.config)
  const [ticked, setTicked] = useState(false)
  const [typedName, setTypedName] = useState("")
  const [role, setRole] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [needsResign, setNeedsResign] = useState(false)

  const document = useQuery({
    queryKey: ["patient-intake", "document", sessionToken, pinned ?? ""],
    queryFn: () => fetchConsentDocument(sessionToken, pinned as string),
    enabled: pinned !== null,
    retry: false,
  })

  const signatures = useQuery({
    queryKey: ["patient-intake", "signatures", sessionToken, assignmentId],
    queryFn: () => listSignatures(sessionToken, assignmentId),
    retry: false,
  })

  const sign = useMutation({
    mutationFn: (body: { signer_role: string; typed_name: string }) =>
      signConsentDocument(sessionToken, assignmentId, {
        item_id: item.id,
        signer_role: body.signer_role,
        typed_name: body.typed_name,
        affirm: true,
      }),
    onSuccess: () => {
      setTicked(false)
      setTypedName("")
      setError(null)
      void signatures.refetch()
      onWrote()
    },
    onError: (raised: unknown) => {
      if (raised instanceof PatientIntakeError && raised.kind === "expired") {
        onSessionLost()
        return
      }
      if (raised instanceof PatientIntakeError && raised.kind === "closed") {
        // A 409 is one of three things, and only one of them is worth its
        // own screen. The server's sentence covers the other two.
        setNeedsResign(raised.serverMessage?.includes("newer version") === true)
        setError(raised.serverMessage ?? CONSENT_SIGN_FAILED)
        return
      }
      setError(
        raised instanceof PatientIntakeError
          ? (raised.serverMessage ?? CONSENT_SIGN_FAILED)
          : CONSENT_SIGN_FAILED,
      )
    },
  })

  // An item the practice published without a pinned version, which the
  // publisher is supposed to prevent. Reads as a step still to come, which
  // is also what the server does with it — it refuses a signature.
  if (pinned === null) return <Unavailable />

  if (document.isError || signatures.isError) {
    return (
      <section data-testid="forms-consent-error" className="py-4">
        <p className="text-sm text-neutral-600">{CONSENT_LOAD_FAILED}</p>
      </section>
    )
  }
  if (document.isPending || signatures.isPending) {
    return (
      <section data-testid="forms-consent-loading" className="py-4">
        <p className="text-sm text-neutral-600">{CONSENT_LOADING}</p>
      </section>
    )
  }

  const askedRoles = document.data.signer_roles.length > 0 ? document.data.signer_roles : ["patient"]
  const mine = signatures.data.filter((row) => row.item_id === item.id)
  const signedRoles = new Set(mine.map((row) => row.signer_role))
  const outstanding = askedRoles.filter((asked) => !signedRoles.has(asked))
  const currentRole = role ?? outstanding[0] ?? askedRoles[0]
  const heading = item.label?.trim() || document.data.title

  const trimmed = typedName.trim()
  const canSign = ticked && trimmed !== "" && outstanding.length > 0

  return (
    <section data-testid="forms-consent" aria-labelledby={`forms-consent-${item.id}`}>
      <h2
        id={`forms-consent-${item.id}`}
        className="text-lg font-semibold text-neutral-900"
      >
        {heading}
      </h2>
      {item.help_text?.trim() && (
        <p data-testid="forms-question-help" className="mt-2 text-sm text-neutral-600">
          {item.help_text.trim()}
        </p>
      )}

      <div
        data-testid="forms-consent-document"
        className="prose-sm mt-4 max-h-80 overflow-y-auto rounded-md border border-neutral-200 p-4 text-sm leading-relaxed text-neutral-800"
        // The server built this by escaping the practice's text first and
        // emitting a fixed set of tags second, so there is no character it
        // could contain that arrives here as markup. See
        // `backend/app/intake/documents.py` for the ordering that makes that
        // true; this is the only place in the portal that sets inner HTML.
        dangerouslySetInnerHTML={{ __html: document.data.rendered_html }}
      />

      {mine.length > 0 && (
        <ul data-testid="forms-consent-signed" className="mt-4 flex flex-col gap-1.5">
          {mine.map((signature) => (
            <SignedRow key={signature.id} signature={signature} />
          ))}
        </ul>
      )}

      {needsResign ? (
        <p data-testid="forms-consent-resign" className="mt-4 text-sm text-neutral-700">
          {CONSENT_NEEDS_RESIGN}
        </p>
      ) : outstanding.length > 0 ? (
        <div className="mt-5 flex flex-col gap-3">
          {askedRoles.length > 1 && (
            <fieldset data-testid="forms-consent-role" className="flex flex-col gap-1.5">
              <legend className="text-sm font-medium text-neutral-800">
                {CONSENT_ROLE_LABEL}
              </legend>
              {askedRoles.map((asked) => (
                <label
                  key={asked}
                  htmlFor={`forms-consent-role-${item.id}-${asked}`}
                  className="flex items-center gap-2 text-sm text-neutral-700"
                >
                  <input
                    type="radio"
                    id={`forms-consent-role-${item.id}-${asked}`}
                    name={`forms-consent-role-${item.id}`}
                    className="h-4 w-4"
                    checked={currentRole === asked}
                    disabled={signedRoles.has(asked)}
                    onChange={() => setRole(asked)}
                  />
                  {roleLabel(asked)}
                </label>
              ))}
              <p className="text-xs text-neutral-600">{CONSENT_GUARDIAN_NOTE}</p>
            </fieldset>
          )}

          <label
            htmlFor={`forms-consent-affirm-${item.id}`}
            className="flex items-start gap-2 text-sm text-neutral-800"
          >
            <input
              type="checkbox"
              id={`forms-consent-affirm-${item.id}`}
              data-testid="forms-consent-affirm"
              className="mt-0.5 h-4 w-4"
              checked={ticked}
              onChange={(e) => setTicked(e.target.checked)}
            />
            <span data-testid="forms-consent-statement">
              {document.data.consent_statement}
            </span>
          </label>

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor={`forms-consent-name-${item.id}`}
              className="block text-sm text-neutral-800"
            >
              {CONSENT_NAME_LABEL}
            </label>
            <Input
              id={`forms-consent-name-${item.id}`}
              data-testid="forms-consent-name"
              value={typedName}
              maxLength={NAME_MAX}
              autoComplete="off"
              onChange={(e) => setTypedName(e.target.value)}
            />
          </div>

          <Button
            data-testid="forms-consent-sign"
            disabled={!canSign || sign.isPending}
            onClick={() => sign.mutate({ signer_role: currentRole, typed_name: trimmed })}
          >
            {sign.isPending ? CONSENT_SIGNING : CONSENT_SIGN}
          </Button>

          {mine.length > 0 && (
            <p data-testid="forms-consent-outstanding" className="text-sm text-neutral-600">
              {outstanding[0] === "guardian" ? CONSENT_AWAITING_GUARDIAN : CONSENT_AWAITING_PATIENT}
            </p>
          )}
        </div>
      ) : null}

      {error && !needsResign && (
        <p data-testid="forms-consent-error-message" className="mt-4 text-sm text-red-600">
          {error}
        </p>
      )}
    </section>
  )
}

function SignedRow({ signature }: { signature: IntakeSignature }) {
  return (
    <li className="flex flex-wrap items-center gap-2 text-sm text-neutral-800">
      <span className="rounded-full bg-primary-50 px-2 py-0.5 text-xs font-medium text-primary-800">
        {CONSENT_SIGNED_BADGE}
      </span>
      <span>{consentSignedBy(signature.signer_typed_name, signedAtLabel(signature.signed_at))}</span>
    </li>
  )
}

/**
 * The review row for a consent item.
 *
 * `signed` comes from the server's own answer about the item, so a document
 * that still needs a second signature reads as unanswered here rather than
 * as done — the same thing the completion walk says about it.
 */
function summaryOf(value: AnswerValue | null): string | null {
  return value?.signed === true ? CONSENT_SIGNED_BADGE : null
}

export const consentDocumentRenderer: ItemRenderer = {
  Component: ConsentDocumentItem,
  // The walk's save route refuses this type outright: its answer names a
  // signature row, and only the signing route may write one.
  answerable: false,
  // But it is a question, not a heading — so it is counted and reviewed.
  writesItself: true,
  label: (item) => item.label?.trim() || CONSENT_REVIEW_LABEL,
  summary: summaryOf,
}
