// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Render slots for content that differs *inside* a shared settings page.
 *
 * The registry slot (`registry.extensions.ts`) answers "which pages exist".
 * This one answers "what extra goes on a page that both builds have" — a row,
 * a card, a toggle that only means something where the surrounding capability
 * exists.
 *
 * Without this, a downstream build that needs one extra card on Availability
 * has only one move left: fork the whole page. That fork then swallows every
 * upstream fix to the other four cards, silently, which is exactly what
 * happened to the settings page this shell replaces.
 *
 * The base build renders nothing from every slot. A downstream build replaces
 * THIS FILE ONLY. Each slot is a real component rendered as `<Slot />`, not a
 * function the page calls, so an implementation may use hooks and fetch its own
 * data without inheriting the calling page's hook order.
 *
 * A slot renders its own card or rows, container included. Pages do not wrap
 * slot output, because a page cannot tell an empty slot from a filled one
 * without calling it.
 */

import type { ReactNode } from "react"
import type { AppointmentTypeResponse, UpdateAppointmentTypeRequest } from "@/types/scheduling"

/** Extra cards on Practice > Availability, below the working-hours grid. */
export function AvailabilityExtras(): ReactNode {
  return null
}


/** An extra card on Practice > Scheduling, below the new-patient flow. */
export function SchedulingEmailReplies(): ReactNode {
  return null
}

/**
 * An extra control on an appointment type's row, next to Self-book.
 *
 * A managed deployment drafts reply emails and needs to know which types it
 * may offer times for in those drafts ("In drafts"); a self-hosted deployment
 * has no draft surface, so this stays absent rather than a toggle nobody's
 * anything reads.
 */
export function SchedulingTypeExtras(_props: {
  appointmentType: AppointmentTypeResponse
  onChange: (patch: UpdateAppointmentTypeRequest) => void
}): ReactNode {
  return null
}

/**
 * Extra cards on Practice > Scheduling, above the public booking pages.
 *
 * Where a deployment's own booking policy lives — what patients may do to a
 * calendar without the clinician in the loop is a different promise from what
 * Pablo may propose, and a deployment that offers the former needs somewhere to
 * say so.
 */
export function SchedulingExtras(): ReactNode {
  return null
}

/** Extra rows on You > Sign-in & security, below the second-factor rows. */
export function SecurityLegalRows(): ReactNode {
  return null
}

/**
 * The recording half of Practice > Sessions & recording.
 *
 * This slot REPLACES rather than appends, so it receives the base build's cards
 * as `fallback` and returns them untouched. The base build has no way to grant
 * recording per account, so everyone who can reach the page gets the controls.
 * A downstream build that does gate it renders the no-access and requested
 * states instead, and simply ignores `fallback`.
 */
export function SessionsRecordingCard({ fallback }: { fallback: ReactNode }): ReactNode {
  return fallback
}
