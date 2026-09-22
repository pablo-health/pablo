// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useAssignIntakePacket } from "@/hooks/useIntakeArtifacts"
import { useIntakeTemplates } from "@/hooks/useIntakePackets"
import { useIssuePortalInvite, usePortalAccess } from "@/hooks/usePortalAccess"
import type { IntakeTemplate, IntakeVersion } from "@/types/intakePackets"

/** One form a practice can send, and the frozen version it would send. */
interface SendableForm {
  templateId: string
  name: string
  version: IntakeVersion
}

/**
 * The newest published version of each form the practice still uses.
 *
 * One entry per form rather than one per version: a clinician sending an
 * intake wants "the intake form", and offering them four frozen versions of
 * it asks a question nobody on this screen is trying to answer. The older
 * versions stay readable on the forms somebody already answered, which is
 * what freezing them was for.
 *
 * An archived form is dropped. A form with no published version is dropped
 * too — it cannot be sent, and the card says so once rather than listing
 * options that would refuse.
 */
export function sendableForms(templates: IntakeTemplate[]): SendableForm[] {
  return templates
    .filter((template) => !template.archived_at)
    .map((template) => {
      const published = template.versions
        .filter((version) => version.published_at !== null)
        .sort((a, b) => b.version - a.version)
      return published.length > 0
        ? { templateId: template.id, name: template.name, version: published[0] }
        : null
    })
    .filter((form): form is SendableForm => form !== null)
}

/**
 * What to tell the clinician after the form went out.
 *
 * The form and the way in are two sends, and only the first is certain by the
 * time this renders. A patient who can already reach the portal needs no
 * second credential, so saying one went would be untrue; a patient with no
 * email or mobile on file has the form waiting and no way to reach it, and
 * that is the one state worth interrupting for.
 */
function deliverySentence(
  invited: boolean,
  inviteError: unknown,
  hadAccess: boolean,
): string {
  if (inviteError) {
    const status = (inviteError as { status?: number } | null)?.status
    if (status === 422) {
      return "The form is ready. This patient needs an email address and a mobile number on file before they can be invited to open it."
    }
    return "The form is ready, but the invite could not be sent. You can send it again from here."
  }
  if (invited) {
    return "Sent. They will get a link by email and a code by text."
  }
  if (hadAccess) {
    return "Sent. It is waiting in their portal."
  }
  return "Sent."
}

/**
 * Why a form would not go out.
 *
 * The picker only ever offers a published version, so a 422 is not a wrong
 * choice — it is a version that stopped being published between the chart
 * loading and the button being pressed. Telling somebody to try again there
 * would send them round a loop that cannot end, so it names what changed.
 */
function assignErrorMessage(error: unknown): string {
  const status = (error as { status?: number } | null)?.status
  if (status === 422) {
    return "This form is no longer published, so it was not sent. Reload the chart to see the current forms."
  }
  return "The form could not be sent. Nothing went to the patient; you can try again."
}

/**
 * Send a patient one of the practice's forms, from their chart.
 *
 * The two halves of starting an intake are one action here. Asking somebody
 * to fill a form in and giving them a way to open it are separate routes and
 * separate failures, but they are not a decision a clinician is making twice
 * — so this sends the form, and mints an invite when the patient has no way
 * in yet. What happened is reported in one sentence, including when only
 * half of it worked.
 *
 * A deployment with no portal answers 404 on the access read, and this
 * quietly sends the form alone: the form is still a real thing to have
 * asked for, and a paragraph explaining an absent portal would be on the
 * screen of every practice that does not run one.
 */
export function SendIntakeForm({ patientId }: { patientId: string }) {
  const { data: templates } = useIntakeTemplates()
  const { data: access } = usePortalAccess(patientId)
  const assign = useAssignIntakePacket(patientId)
  const invite = useIssuePortalInvite(patientId)

  const [selected, setSelected] = useState<string>("")
  const [sent, setSent] = useState<string | null>(null)

  const forms = sendableForms(templates ?? [])
  const chosen = forms.find((form) => form.version.id === selected) ?? forms[0]

  // A patient with a live session or an unused invite already has a way in.
  // The portal being absent reads the same as "no invite needed" on purpose:
  // there is nothing to mint against a portal that is not there.
  const hasWayIn = !access || access.invite_outstanding || access.live_sessions > 0

  // Nothing published to send is not a state worth a sentence on the chart.
  // A practice mid-setup would carry that sentence on every chart it has,
  // and the place to act on it is where forms are built, not here.
  if (forms.length === 0) return null

  async function send() {
    if (!chosen) return
    setSent(null)
    try {
      await assign.mutateAsync(chosen.version.id)
    } catch {
      // The mutation's own error state renders the sentence. Swallowed here
      // so a form that could not be sent is a message on the screen rather
      // than an unhandled rejection, and so no invite is minted for a form
      // that never went out.
      return
    }

    let invited = false
    let inviteError: unknown = null
    if (!hasWayIn) {
      try {
        await invite.mutateAsync()
        invited = true
      } catch (error) {
        // Caught rather than read back off the hook: the mutation's own error
        // state has not re-rendered by the time this line runs, so asking it
        // would report the previous attempt's outcome.
        inviteError = error
      }
    }
    setSent(deliverySentence(invited, inviteError, hasWayIn))
  }

  const busy = assign.isPending || invite.isPending

  return (
    <div className="space-y-2" data-testid="send-intake-form">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={chosen?.version.id ?? ""} onValueChange={setSelected}>
          <SelectTrigger aria-label="Form" className="w-64">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {forms.map((form) => (
              <SelectItem key={form.version.id} value={form.version.id}>
                {form.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button onClick={send} disabled={busy} data-testid="send-intake-form-button">
          {busy ? "Sending…" : "Send form"}
        </Button>
      </div>

      {assign.isError && (
        <p className="text-sm text-red-700" data-testid="send-intake-form-error">
          {assignErrorMessage(assign.error)}
        </p>
      )}

      {sent && (
        <p className="text-sm text-neutral-700" data-testid="send-intake-form-sent">
          {sent}
        </p>
      )}
    </div>
  )
}
