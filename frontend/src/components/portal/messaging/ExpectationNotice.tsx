// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What a patient should expect from messaging, said before they write.
 *
 * Three sentences, always all three, always in this order: messaging is
 * not for an emergency, here is who to reach if it is one, and here is
 * roughly when to expect a reply.
 *
 * The strings are written here and nowhere else. They are never drafted
 * by a model, never assembled from a template, and the crisis line is
 * never conditional — no prop, no configuration and no state hides it.
 * A practice can say more about its own reply times (see `slaText`); it
 * cannot say less than this.
 *
 * The default reply-time sentence is imprecise on purpose. Promising a
 * number the practice has not agreed to would be a commitment made by
 * software on a clinician's behalf.
 */

export const NOT_FOR_EMERGENCIES = "Messaging isn't for emergencies."

export const CRISIS_LINE =
  "If you need help right away, call 911, or call or text 988 (Suicide & Crisis Lifeline)."

export const DEFAULT_RESPONSE_TIME =
  "Your therapist typically replies within a few business days."

export interface ExpectationNoticeProps {
  /** What the practice has configured, when it has configured anything. */
  slaText?: string | null
}

export function ExpectationNotice({ slaText }: ExpectationNoticeProps) {
  const responseTime = slaText?.trim() ? slaText.trim() : DEFAULT_RESPONSE_TIME

  return (
    <div
      role="note"
      aria-label="What to expect from messaging"
      data-testid="portal-messaging-expectation-notice"
      className="rounded-md border border-neutral-200 bg-neutral-50 p-3 text-sm text-neutral-700"
    >
      <p>{NOT_FOR_EMERGENCIES}</p>
      <p className="mt-1">{CRISIS_LINE}</p>
      <p className="mt-1" data-testid="portal-messaging-response-time">
        {responseTime}
      </p>
    </div>
  )
}
