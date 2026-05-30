// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SessionGeneratingOverlay Component
 *
 * Full-screen overlay shown while a SOAP note is being generated after
 * transcript upload. Polls the session until it reaches pending_review
 * (then navigates) or failed (then shows a retryable error card).
 *
 * Design: Pablo bear image + rotating encouraging copy + spinner.
 * The overlay sits above the dashboard shell via fixed positioning so
 * the clinician stays oriented without a jarring navigation.
 */

"use client"

import { useState, useEffect, useCallback } from "react"
import { useRouter } from "next/navigation"
import Image from "next/image"
import { Loader2, AlertCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { useSession, useSessionProcessing } from "@/hooks/useSessions"

// ---------------------------------------------------------------------------
// Rotating copy — warm, clinically literate, never cheesy.
// Each line is a brief moment of reassurance while the note is being drafted.
// ---------------------------------------------------------------------------
const PROCESSING_LINES = [
  "Reading between the lines…",
  "Connecting the threads of your conversation…",
  "Good notes are the difference between remembering and reconstructing.",
  "Listening for what matters most…",
  "Drafting something you'll only need to glance at.",
  "Finding the signal in the session…",
  "Clinical detail takes a moment to get right.",
  "Translating conversation into documentation…",
  "Holding the whole session in mind at once.",
  "Almost there — careful work takes a little time.",
  "Turning your transcript into a note worth keeping.",
  "Picking up every thread before the draft begins.",
]

const COPY_INTERVAL_MS = 3500

interface SessionGeneratingOverlayProps {
  /**
   * The patient whose most-recent in-flight session we are tracking.
   * Pass `null` to hide the overlay.
   */
  patientId: string | null
  /** Called once the session has reached pending_review or failed. */
  onDone?: () => void
  className?: string
}

/**
 * SessionGeneratingOverlay
 *
 * Rendered by the parent component after a successful transcript upload.
 * It polls via two hooks:
 *
 *  1. `useSessionProcessing(patientId)` — polls the session list until the
 *     in-flight session's id becomes visible (within ~1-3 s of POST start).
 *  2. `useSession(sessionId, { refetchInterval })` — polls the detail until
 *     status leaves `queued`/`processing`.
 *
 * Once status reaches `pending_review`, it navigates to the session detail
 * page.  If status is `failed`, it shows a friendly error state with a retry
 * link.
 */
export function SessionGeneratingOverlay({
  patientId,
  onDone,
  className,
}: SessionGeneratingOverlayProps) {
  const router = useRouter()
  const [copyIndex, setCopyIndex] = useState(0)

  // Rotate the encouraging copy, respecting prefers-reduced-motion.
  useEffect(() => {
    if (typeof window !== "undefined") {
      const mq = window.matchMedia("(prefers-reduced-motion: reduce)")
      if (mq.matches) return
    }

    const id = setInterval(() => {
      setCopyIndex((i) => (i + 1) % PROCESSING_LINES.length)
    }, COPY_INTERVAL_MS)

    return () => clearInterval(id)
  }, [])

  // Stage 1 — watch the session list for the newly-created session id.
  const { sessionId, timedOut } = useSessionProcessing(patientId)

  // Stage 2 — poll the detail once we have a session id.
  const { data: session } = useSession(
    sessionId ?? "__none__",
    undefined,
    {
      enabled: sessionId !== null,
      refetchInterval: (query) => {
        const s = query.state.data?.status
        return s === "queued" || s === "processing" ? 3000 : false
      },
    },
  )

  const handleNavigate = useCallback(() => {
    if (session?.id) {
      onDone?.()
      router.push(`/dashboard/sessions/${session.id}`)
    }
  }, [session, onDone, router])

  // Navigate automatically when the note is ready.
  useEffect(() => {
    if (session?.status === "pending_review") {
      handleNavigate()
    }
  }, [session, handleNavigate])

  if (!patientId) return null

  const showError = session?.status === "failed" || timedOut

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={showError ? "Note generation failed" : "Generating SOAP note"}
      className={cn(
        "fixed inset-0 z-40 flex flex-col items-center justify-center gap-6",
        "bg-background/90 backdrop-blur-sm",
        className,
      )}
    >
      {/* Pablo bear */}
      <Image
        src="/pablo-today.webp"
        alt="Pablo"
        width={128}
        height={128}
        className="drop-shadow-lg"
        priority
      />

      {showError ? (
        /* Error state */
        <div className="flex flex-col items-center text-center gap-4">
          <AlertCircle className="w-10 h-10 text-destructive" aria-hidden="true" />
          <div className="space-y-1">
            <p className="font-display font-semibold text-neutral-900 text-lg">
              Note generation didn&apos;t complete
            </p>
            <p className="text-sm text-neutral-600">
              Please try uploading the transcript again.
            </p>
          </div>
          <div className="flex gap-3">
            <Button
              variant="outline"
              onClick={() => {
                onDone?.()
              }}
            >
              Dismiss
            </Button>
            {session?.id && (
              <Button
                onClick={() => {
                  onDone?.()
                  router.push(`/dashboard/sessions/${session.id}`)
                }}
              >
                View session
              </Button>
            )}
          </div>
        </div>
      ) : (
        /* Processing state */
        <div className="flex flex-col items-center gap-4">
          <div className="text-center space-y-1">
            <p className="font-display font-semibold text-neutral-900 text-lg">
              Pablo is writing your note
            </p>
            <p
              className="text-sm text-neutral-600 transition-opacity duration-500"
              aria-live="off"
            >
              {PROCESSING_LINES[copyIndex]}
            </p>
          </div>
          <Loader2
            className="w-8 h-8 text-primary-600 animate-spin"
            aria-hidden="true"
          />
        </div>
      )}
    </div>
  )
}
