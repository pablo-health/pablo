// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One line, after Google Calendar connects, saying where video sessions go.
 *
 * Connecting is what makes Google Meet possible, and a clinician with no video
 * default is given Meet by the connect itself (the server does it). This line
 * only says so. It is not a step and asks nothing, because every question
 * added here costs finished calendar connections.
 *
 * The one case it has to handle with care is a saved default that is not
 * connected, almost always a Zoom saved before there was a choice. That may
 * still be a real choice, so it is never changed silently: the line says Meet,
 * "Keep Zoom" leaves things as they are, and continuing past the step is the
 * clinician's tap that makes Meet the default (see the wizard's Continue).
 */

"use client"

import { useQuery } from "@tanstack/react-query"
import { Video } from "lucide-react"
import { listTelehealthProviders } from "@/lib/api/telehealth"

export const TELEHEALTH_PROVIDERS_QUERY_KEY = ["telehealth", "providers"]

const GOOGLE_MEET = "google_meet"

export type VideoDefaultOffer =
  /** Nothing to say: Meet is not connected, or another connected service is the default. */
  | { kind: "none" }
  /** Meet is the default already. */
  | { kind: "meet" }
  /** A saved default that is not connected; Meet on continue unless kept. */
  | { kind: "offer"; keepLabel: string }

/** What the line should say, from the clinician's services right now. */
export function useVideoDefaultOffer(enabled: boolean): VideoDefaultOffer {
  const { data } = useQuery({
    queryKey: TELEHEALTH_PROVIDERS_QUERY_KEY,
    queryFn: listTelehealthProviders,
    enabled,
  })
  if (!data) return { kind: "none" }
  const meetConnected = data.providers.some(
    (provider) => provider.id === GOOGLE_MEET && provider.connected,
  )
  if (!meetConnected) return { kind: "none" }
  if (data.default_provider === GOOGLE_MEET) return { kind: "meet" }
  if (data.default_provider !== null || data.stored_default === null) return { kind: "none" }
  const saved = data.providers.find((provider) => provider.id === data.stored_default)
  return {
    kind: "offer",
    keepLabel: saved ? `Keep ${saved.display_name}` : "Keep my current setting",
  }
}

interface VideoDefaultLineProps {
  offer: VideoDefaultOffer
  /** The clinician tapped Keep: the saved default stays. */
  kept: boolean
  onKeep: () => void
  error: string | null
}

export function VideoDefaultLine({ offer, kept, onKeep, error }: VideoDefaultLineProps) {
  if (offer.kind === "none" || (offer.kind === "offer" && kept)) return null
  return (
    <div data-testid="video-default-line" className="mb-4 space-y-1">
      <p className="flex items-center gap-2 text-sm text-neutral-700">
        <Video className="h-4 w-4" aria-hidden="true" />
        <span>Video sessions get a Google Meet link.</span>
        {offer.kind === "offer" ? (
          <button
            type="button"
            data-testid="video-default-keep"
            onClick={onKeep}
            className="font-medium text-muted-foreground underline underline-offset-2 hover:text-neutral-900"
          >
            {offer.keepLabel}
          </button>
        ) : null}
      </p>
      {error ? <p className="text-sm text-red-600">{error}</p> : null}
    </div>
  )
}
