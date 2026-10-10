// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Button } from "@/components/ui/button"
import { SupportContactLine } from "./SupportContactLine"

interface PanelLoadErrorProps {
  /** What did not load, as a sentence: "Today's sessions didn't load." */
  message: string
  onRetry: () => void
}

/**
 * The body of a panel whose data failed to load: what failed, a retry, and
 * where to write if retrying doesn't help.
 *
 * A panel shows this instead of its empty state. An empty state on a failed
 * load says something untrue ("No sessions today") about data it never got.
 */
export function PanelLoadError({ message, onRetry }: PanelLoadErrorProps) {
  return (
    <div role="alert" className="flex flex-col items-center gap-3 py-6 text-center">
      <p className="text-sm text-neutral-700">{message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Try again
      </Button>
      <SupportContactLine />
    </div>
  )
}
