// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One line, after Google Calendar connects, saying video sessions get Meet.
 *
 * Connecting is what makes Google Meet possible, and the connect itself makes
 * Meet the default for a clinician with none that works (the server does it;
 * a stored Zoom is never changed). This line only reports that, and only when
 * the clinician's default really is Meet. It is not a step and asks nothing:
 * every question added here costs finished calendar connections, and the
 * default is changed on the Video sessions card in Settings.
 */

"use client"

import { useQuery } from "@tanstack/react-query"
import { Video } from "lucide-react"
import { listTelehealthProviders } from "@/lib/api/telehealth"

export const TELEHEALTH_PROVIDERS_QUERY_KEY = ["telehealth", "providers"]

const GOOGLE_MEET = "google_meet"

/** Whether Meet is the default the clinician's sessions get, right now. */
export function useMeetIsDefault(enabled: boolean): boolean {
  const { data } = useQuery({
    queryKey: TELEHEALTH_PROVIDERS_QUERY_KEY,
    queryFn: listTelehealthProviders,
    enabled,
  })
  return data?.default_provider === GOOGLE_MEET
}

export function VideoDefaultLine() {
  return (
    <p data-testid="video-default-line" className="mb-4 flex items-center gap-2 text-sm text-neutral-700">
      <Video className="h-4 w-4" aria-hidden="true" />
      <span>Video sessions get a Google Meet link.</span>
    </p>
  )
}
