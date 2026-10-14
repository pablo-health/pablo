// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { PeopleWords } from "@/lib/peopleTerm"
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

/** What the acknowledgment screen says: a heading, then a line or two. */
export interface DeliveryOutcome {
  heading: string
  lines: string[]
  /** False when something the clinician asked for did not go out. */
  complete: boolean
}

export interface DeliveryFacts {
  formCount: number
  invited: boolean
  inviteError: unknown
  /** The client could already sign in before this send. */
  hadAccess: boolean
  email: string | null | undefined
  phone: string | null | undefined
  /** False on a deployment that serves no portal at all. */
  portalServed?: boolean
}

/**
 * What to tell the clinician after pressing Send.
 *
 * The forms and the way in are separate sends, and only the forms are
 * certain by the time this renders. The heading says what actually went out
 * and never more: a failed invitation is named in it, not left to a line the
 * clinician might not read. A client who can already reach the portal needs
 * no second credential, so saying one went would be untrue; a client with no
 * email or mobile on file has the forms waiting and no way to reach them, and
 * that is the one state worth interrupting for.
 */
/** "Packet" or "Packets", by how many went out. */
function packetsWord(count: number): string {
  return count === 1 ? "Packet" : "Packets"
}

function sentWord(count: number): string {
  return `${packetsWord(count)} sent`
}

export function deliveryOutcome(facts: DeliveryFacts, people: PeopleWords): DeliveryOutcome {
  const { formCount, invited, inviteError, hadAccess, email, phone } = facts
  const forms = formCount > 0
  if (inviteError) {
    const status = (inviteError as { status?: number } | null)?.status
    const heading = forms ? `${sentWord(formCount)}. The invitation didn't go out.` : "Invitation not sent"
    if (status === 422) {
      return {
        heading,
        lines: [
          `This ${people.one} needs an email address and a mobile number on file before they can be invited.`,
        ],
        complete: false,
      }
    }
    return {
      heading,
      lines: [forms ? "You can send the invitation again from the chart." : "You can try again from the chart."],
      complete: false,
    }
  }
  if (invited) {
    const lines = [`They'll get a link by email at ${email} and a code by text at ${phone}.`]
    if (forms) lines.push("You'll see their answers on the Intake tab once they're done.")
    return { heading: forms ? `${packetsWord(formCount)} and invitation sent` : "Invitation sent", lines, complete: true }
  }
  if (hadAccess && forms) {
    return { heading: sentWord(formCount), lines: ["They're waiting in their portal."], complete: true }
  }
  if (facts.portalServed === false) {
    // A deployment with no portal: forms are filled in with the practice,
    // and there is no invitation to be missing.
    return { heading: sentWord(formCount), lines: [], complete: true }
  }
  // Forms on the chart and no way in for the client: the one state worth
  // interrupting for (see above), so it is not shown as done.
  return {
    heading: sentWord(formCount),
    lines: ["They'll need an invitation to the portal to open them."],
    complete: false,
  }
}

/**
 * Why a form would not go out.
 *
 * The picker only ever offers a published version, so a 422 is not a wrong
 * choice — it is a version that stopped being published between the screen
 * loading and the button being pressed. Telling somebody to try again there
 * would send them round a loop that cannot end, so it names what changed.
 */
export function assignErrorMessage(
  error: unknown,
  alreadySent: string[],
  people: PeopleWords,
): string {
  const status = (error as { status?: number } | null)?.status
  const sofar =
    alreadySent.length > 0 ? ` ${alreadySent.join(", ")} went out; the rest did not.` : ""
  if (status === 422) {
    return `A packet is no longer published, so it was not sent.${sofar} Reload to see the current packets.`
  }
  return alreadySent.length > 0
    ? `Not every packet could be sent.${sofar} You can send the others from the chart.`
    : `The packets could not be sent. Nothing went to the ${people.one}; you can try again.`
}
