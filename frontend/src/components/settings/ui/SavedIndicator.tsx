// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Check } from "lucide-react"
import { cn } from "@/lib/utils"
import { useCallback, useEffect, useRef, useState } from "react"

const VISIBLE_MS = 1800

/**
 * "Saved", top right of the page, fading out on its own.
 *
 * Most settings rows save on change with no button, so this is the only
 * confirmation the user gets that the change reached the server.
 */
export function useSavedFlash(): { saved: boolean; flashSaved: () => void } {
  const [saved, setSaved] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])

  const flashSaved = useCallback(() => {
    setSaved(true)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setSaved(false), VISIBLE_MS)
  }, [])

  return { saved, flashSaved }
}

export function SavedIndicator({ saved }: { saved: boolean }) {
  return (
    <div
      aria-live="polite"
      className={cn(
        "flex shrink-0 items-center gap-1.5 text-[12.5px] font-semibold text-secondary-600 transition-opacity duration-300",
        saved ? "opacity-100" : "opacity-0"
      )}
    >
      {saved && (
        <>
          <Check className="h-3.5 w-3.5" aria-hidden="true" />
          Saved
        </>
      )}
    </div>
  )
}
