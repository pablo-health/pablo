// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Image from "next/image"
import { usePrefersReducedMotion } from "@/hooks/usePrefersReducedMotion"
import { cn } from "@/lib/utils"

interface PabloSpinnerProps {
  /** What Pablo is doing, read out and shown beside the bear. */
  label: string
  /** Diameter of the bear, in px. */
  size?: number
  className?: string
}

/**
 * The bear with a ring turning around it, for a wait the reader should see
 * is in progress — a bare "Loading…" line reads as stuck. With reduced
 * motion the ring is left out entirely and the bear and label stay still;
 * `motion-reduce:` on the ring covers the moment before the preference has
 * been read on the client.
 */
export function PabloSpinner({ label, size = 32, className }: PabloSpinnerProps) {
  const reducedMotion = usePrefersReducedMotion()
  return (
    <span
      role="status"
      data-motion={reducedMotion ? "static" : "animated"}
      className={cn("inline-flex items-center gap-3", className)}
    >
      <span className="relative inline-flex shrink-0" style={{ width: size, height: size }}>
        <Image
          src="/pablo-tie.webp"
          alt=""
          width={size}
          height={size}
          className="rounded-full object-cover"
        />
        {reducedMotion ? null : (
          <span
            aria-hidden="true"
            data-testid="pablo-spinner-ring"
            className="absolute -inset-1 rounded-full border-2 border-primary-200 border-t-primary-600 animate-spin motion-reduce:hidden"
          />
        )}
      </span>
      <span className="text-sm text-neutral-600">{label}</span>
    </span>
  )
}
