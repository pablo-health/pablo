// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { useState, type ReactNode } from "react"
import { AlertCircle, CheckCircle2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { useAssignIntakePacket } from "@/hooks/useIntakeArtifacts"
import { useIntakeTemplates } from "@/hooks/useIntakePackets"
import { useInviteTemplate } from "@/hooks/useInviteTemplate"
import { usePatient } from "@/hooks/usePatients"
import { useIssuePortalInvite, usePortalAccess } from "@/hooks/usePortalAccess"
import { InviteEmailPreview } from "./InviteEmailPreview"
import {
  assignErrorMessage,
  deliveryOutcome,
  sendableForms,
  type DeliveryOutcome,
} from "./sendable"

type Step = "choose" | "review" | "sent"

interface SendFormsFlowProps {
  patientId: string
  /** Leaves the flow: closes the dialog it sits in. */
  onDone: () => void
  /** What the way out of the first step says — "Not now" for a new client. */
  dismissLabel?: string
  /**
   * The dialog's own title, shown while choosing and reviewing. Passed in
   * rather than drawn by the dialog, so the acknowledgment can replace it: a
   * question like "What should Robin do next?" still sitting above "Invitation
   * sent" reads as the screen having half-updated.
   */
  header?: ReactNode
  /** Offer a way to the client's chart from the acknowledgment. */
  chartHref?: string
}

/**
 * Choose what a client should do, review it, send it.
 *
 * The same three steps whether the practice has just added the client or is
 * sending from the chart. Choosing is which published forms, and whether to
 * give portal access; reviewing lists exactly that, with the invitation
 * email a press away; sending assigns the forms and then, if asked, mints
 * the invitation — so an invitation never announces forms that did not go.
 *
 * A deployment with no portal answers 404 on the access read. Then there is
 * no access to offer, and the forms go on their own.
 */
export function SendFormsFlow({
  patientId,
  onDone,
  dismissLabel = "Cancel",
  header,
  chartHref,
}: SendFormsFlowProps) {
  const { data: templates, isLoading: templatesLoading } = useIntakeTemplates()
  const { data: patient } = usePatient(patientId)
  const { data: access, isError: noPortal } = usePortalAccess(patientId)
  const { data: wording } = useInviteTemplate()
  const assign = useAssignIntakePacket(patientId)
  const invite = useIssuePortalInvite(patientId)

  const forms = sendableForms(templates ?? [])
  const hasWayIn = !!access && (access.invite_outstanding || access.live_sessions > 0)
  const contactComplete = !!patient?.email && !!patient?.phone
  const portalOff = !!access && !access.portal_enabled
  const canInvite = !noPortal && !!access && !portalOff && !hasWayIn && contactComplete

  const [step, setStep] = useState<Step>("choose")
  const [selected, setSelected] = useState<string[]>([])
  const [inviteChoice, setInviteChoice] = useState<boolean | null>(null)
  const [showEmail, setShowEmail] = useState(false)
  const [outcome, setOutcome] = useState<DeliveryOutcome | null>(null)
  const [sentForms, setSentForms] = useState<string[]>([])
  const [failure, setFailure] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Default to inviting whenever an invitation is possible; the clinician
  // can untick it. Held as null until they do, so a slow access read does
  // not freeze the default at "no".
  const sendInvite = canInvite && (inviteChoice ?? true)
  const chosen = forms.filter((form) => selected.includes(form.version.id))
  const nothingChosen = chosen.length === 0 && !sendInvite

  function toggle(versionId: string, on: boolean) {
    setSelected((current) =>
      on ? [...current, versionId] : current.filter((id) => id !== versionId),
    )
  }

  async function send() {
    setBusy(true)
    setFailure(null)
    const sent: string[] = []
    for (const form of chosen) {
      try {
        await assign.mutateAsync(form.version.id)
        sent.push(form.name)
      } catch (error) {
        // No invitation for forms that did not all go out: the email would
        // point the client at a list that is not what the practice meant.
        setFailure(assignErrorMessage(error, sent))
        setBusy(false)
        return
      }
    }

    let invited = false
    let inviteError: unknown = null
    if (sendInvite) {
      try {
        await invite.mutateAsync()
        invited = true
      } catch (error) {
        inviteError = error
      }
    }
    setOutcome(
      deliveryOutcome({
        formCount: chosen.length,
        invited,
        inviteError,
        hadAccess: hasWayIn,
        email: patient?.email,
        phone: patient?.phone,
      }),
    )
    setSentForms(chosen.map((form) => form.name))
    setStep("sent")
    setBusy(false)
  }

  if (step === "sent" && outcome) {
    const Icon = outcome.complete ? CheckCircle2 : AlertCircle
    return (
      <div className="space-y-4" data-testid="send-forms-sent">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <Icon
              className={`h-6 w-6 shrink-0 ${outcome.complete ? "text-green-600" : "text-amber-600"}`}
              aria-hidden="true"
            />
            <DialogTitle data-testid="send-forms-heading">{outcome.heading}</DialogTitle>
          </div>
          {patient && (
            <p className="text-sm text-neutral-600">
              For {patient.first_name} {patient.last_name}
            </p>
          )}
        </DialogHeader>
        {outcome.lines.length > 0 && (
          <div className="space-y-1 text-sm text-neutral-800" data-testid="send-forms-outcome">
            {outcome.lines.map((line) => (
              <p key={line}>{line}</p>
            ))}
          </div>
        )}
        {sentForms.length > 0 && (
          <section className="space-y-1">
            <h3 className="text-sm font-semibold text-neutral-900">Forms sent</h3>
            <ul className="list-disc pl-5 text-sm text-neutral-800" data-testid="send-forms-sent-list">
              {sentForms.map((name) => (
                <li key={name}>{name}</li>
              ))}
            </ul>
          </section>
        )}
        <div className="flex justify-end gap-2">
          {chartHref && (
            <Button asChild variant="outline">
              <Link href={chartHref}>Open client&rsquo;s chart</Link>
            </Button>
          )}
          <Button onClick={onDone}>Done</Button>
        </div>
      </div>
    )
  }

  if (step === "review") {
    return (
      <div className="space-y-4" data-testid="send-forms-review">
        {header}
        <section className="space-y-1">
          <h3 className="text-sm font-semibold text-neutral-900">Forms</h3>
          {chosen.length > 0 ? (
            <ul className="list-disc pl-5 text-sm text-neutral-800" data-testid="send-forms-review-list">
              {chosen.map((form) => (
                <li key={form.version.id}>{form.name}</li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-neutral-600">No forms.</p>
          )}
        </section>

        <section className="space-y-2">
          <h3 className="text-sm font-semibold text-neutral-900">Portal</h3>
          <p className="text-sm text-neutral-800" data-testid="send-forms-review-portal">
            {sendInvite
              ? `A sign-in link goes to ${patient?.email}, and a code by text to ${patient?.phone}.`
              : hasWayIn
                ? "They can already sign in to the portal."
                : "No invitation."}
          </p>
          {sendInvite && wording?.editable && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setShowEmail((open) => !open)}
              aria-expanded={showEmail}
            >
              {showEmail ? "Hide email" : "Preview email"}
            </Button>
          )}
          {sendInvite && showEmail && (
            <InviteEmailPreview
              patientId={patientId}
              versionIds={chosen.map((form) => form.version.id)}
            />
          )}
        </section>

        {failure && (
          <p className="text-sm text-red-700" data-testid="send-forms-error">
            {failure}
          </p>
        )}

        <div className="flex justify-between gap-2">
          <Button type="button" variant="outline" onClick={() => setStep("choose")} disabled={busy}>
            Back
          </Button>
          <Button type="button" onClick={send} disabled={busy} data-testid="send-forms-send">
            {busy ? "Sending…" : "Send"}
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4" data-testid="send-forms-choose">
      {header}
      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-neutral-900">Forms to fill in</h3>
        {templatesLoading ? (
          <p className="text-sm text-neutral-500">Loading forms…</p>
        ) : forms.length > 0 ? (
          <ul className="space-y-2">
            {forms.map((form) => {
              const id = `send-form-${form.version.id}`
              return (
                <li key={form.version.id} className="flex items-center gap-2">
                  <Checkbox
                    id={id}
                    checked={selected.includes(form.version.id)}
                    onCheckedChange={(on) => toggle(form.version.id, on === true)}
                  />
                  <Label htmlFor={id} className="font-normal">
                    {form.name}
                  </Label>
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="text-sm text-neutral-600" data-testid="send-forms-none-published">
            No forms are published yet.{" "}
            <Link href="/dashboard/settings/portal" className="font-medium underline">
              Set up forms
            </Link>
          </p>
        )}
      </section>

      {/* Waits for the client's record too: "add an email address" before it
          has loaded would be a claim nobody checked. */}
      {!noPortal && access && patient && (
        <section className="space-y-2">
          <h3 className="text-sm font-semibold text-neutral-900">Portal</h3>
          {portalOff ? (
            <p className="text-sm text-neutral-600" data-testid="send-forms-portal-off">
              To invite them to the portal,{" "}
              <Link href="/dashboard/settings/portal" className="font-medium underline">
                turn on the client portal
              </Link>
              .
            </p>
          ) : hasWayIn ? (
            <p className="text-sm text-neutral-600">They can already sign in to the portal.</p>
          ) : contactComplete ? (
            <div className="flex items-center gap-2">
              <Checkbox
                id="send-forms-invite"
                checked={sendInvite}
                onCheckedChange={(on) => setInviteChoice(on === true)}
              />
              <Label htmlFor="send-forms-invite" className="font-normal">
                Invite them to the portal
              </Label>
            </div>
          ) : (
            <p className="text-sm text-neutral-600" data-testid="send-forms-contact-missing">
              To invite them to the portal, add an email address and a mobile number to their
              record.
            </p>
          )}
        </section>
      )}

      <div className="flex justify-between gap-2">
        <Button type="button" variant="outline" onClick={onDone}>
          {dismissLabel}
        </Button>
        <Button type="button" onClick={() => setStep("review")} disabled={nothingChosen}>
          Review
        </Button>
      </div>
    </div>
  )
}
