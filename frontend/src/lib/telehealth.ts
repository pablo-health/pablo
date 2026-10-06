// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { AppointmentResponse } from "@/types/scheduling"

/** Place-of-service codes for a visit held by video or phone. */
const TELEHEALTH_PLACES = new Set(["02", "10"])

/**
 * Whether a visit is telehealth: a video service, a video link, or a
 * telehealth place of service. Mirrors `is_telehealth` on the server, which
 * is what decides.
 */
export function isTelehealth(
  appointment: Pick<AppointmentResponse, "provider" | "video_link" | "place_of_service">,
): boolean {
  return Boolean(
    appointment.provider ||
      appointment.video_link ||
      (appointment.place_of_service && TELEHEALTH_PLACES.has(appointment.place_of_service)),
  )
}
