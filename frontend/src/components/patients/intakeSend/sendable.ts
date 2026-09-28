// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { IntakeTemplate, IntakeVersion } from "@/types/intakePackets"

/** One form a practice can send, and the frozen version it would send. */
export interface SendableForm {
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
 * An archived form is dropped, and so is one with no published version — it
 * cannot be sent.
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
 * What to tell the clinician after pressing Send.
 *
 * The forms and the way in are separate sends, and only the forms are
 * certain by the time this renders. A client who can already reach the
 * portal needs no second credential, so saying one went would be untrue; a
 * client with no email or mobile on file has the forms waiting and no way to
 * reach them, and that is the one state worth interrupting for.
 */
export function deliverySentence(
  formCount: number,
  invited: boolean,
  inviteError: unknown,
  hadAccess: boolean,
): string {
  const lead = formCount > 0 ? "Sent." : "Invitation sent."
  if (inviteError) {
    const status = (inviteError as { status?: number } | null)?.status
    if (status === 422) {
      return "The forms are ready. This client needs an email address and a mobile number on file before they can be invited to open them."
    }
    return formCount > 0
      ? "The forms are ready, but the invitation could not be sent. You can send it again from the chart."
      : "The invitation could not be sent. You can try again from the chart."
  }
  if (invited) return `${lead} They will get a link by email and a code by text.`
  if (hadAccess && formCount > 0) return "Sent. The forms are waiting in their portal."
  return "Sent."
}

/**
 * Why a form would not go out.
 *
 * The picker only ever offers a published version, so a 422 is not a wrong
 * choice — it is a version that stopped being published between the screen
 * loading and the button being pressed. Telling somebody to try again there
 * would send them round a loop that cannot end, so it names what changed.
 */
export function assignErrorMessage(error: unknown, alreadySent: string[]): string {
  const status = (error as { status?: number } | null)?.status
  const sofar =
    alreadySent.length > 0 ? ` ${alreadySent.join(", ")} went out; the rest did not.` : ""
  if (status === 422) {
    return `A form is no longer published, so it was not sent.${sofar} Reload to see the current forms.`
  }
  return alreadySent.length > 0
    ? `Not every form could be sent.${sofar} You can send the others from the chart.`
    : "The forms could not be sent. Nothing went to the client; you can try again."
}
