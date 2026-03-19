// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SourceBadge Component
 *
 * Visual indicator for whether an AI-generated SOAP claim is backed by
 * transcript evidence (verified) or lacks source references (unverified).
 * Supports confidence-level gradations when verification data is available.
 */

import { cn } from "@/lib/utils"
import { useConfig } from "@/lib/config"

interface SourceBadgeProps {
  sourceSegmentIds: number[]
  confidenceLevel?: string
  confidenceScore?: number
  className?: string
}

const CONFIDENCE_STYLES: Record<string, { text: string; className: string }> = {
  high: {
    text: "verified",
    className: "text-green-600 font-medium",
  },
  medium: {
    text: "sourced",
    className: "text-neutral-400 font-normal",
  },
  low: {
    text: "low confidence",
    className: "text-orange-600 font-medium",
  },
  unverified: {
    text: "(unverified)",
    className: "text-yellow-700 font-medium",
  },
}

export function SourceBadge({
  sourceSegmentIds,
  confidenceLevel,
  confidenceScore,
  className,
}: SourceBadgeProps) {
  const config = useConfig()
  if (!config.showVerificationBadges) return null

  // When confidence_level is a non-empty string, use confidence-based display
  if (confidenceLevel && confidenceLevel in CONFIDENCE_STYLES) {
    const style = CONFIDENCE_STYLES[confidenceLevel]
    const title = confidenceScore != null
      ? `Confidence: ${Math.round(confidenceScore * 100)}%`
      : undefined
    return (
      <span
        className={cn("inline-flex items-center text-xs ml-2", style.className, className)}
        title={title}
      >
        {style.text}
      </span>
    )
  }

  // Fallback: binary behavior based on sourceSegmentIds
  const isVerified = sourceSegmentIds.length > 0

  if (isVerified) {
    return (
      <span
        className={cn(
          "inline-flex items-center text-xs text-neutral-400 font-normal ml-2",
          className,
        )}
      >
        sourced
      </span>
    )
  }

  return (
    <span
      className={cn(
        "inline-flex items-center text-xs text-yellow-700 font-medium ml-2",
        className,
      )}
    >
      (unverified)
    </span>
  )
}

interface SourceHighlightProps {
  sourceSegmentIds: number[]
  confidenceLevel?: string
  children: React.ReactNode
  className?: string
  onClick?: () => void
  role?: string
  tabIndex?: number
  onKeyDown?: (e: React.KeyboardEvent) => void
}

const HIGHLIGHT_BORDERS: Record<string, string> = {
  high: "border-l-2 border-green-400 pl-3",
  low: "border-l-2 border-orange-400 bg-orange-50/30 pl-3",
  unverified: "border-l-2 border-yellow-400 bg-yellow-50/50 pl-3",
}

export function SourceHighlight({
  sourceSegmentIds,
  confidenceLevel,
  children,
  className,
  onClick,
  role,
  tabIndex,
  onKeyDown,
}: SourceHighlightProps) {
  const config = useConfig()
  const interactiveProps = { onClick, role, tabIndex, onKeyDown }

  // When flag is off, render children without any verification styling
  if (!config.showVerificationBadges) {
    return <div className={className} {...interactiveProps}>{children}</div>
  }

  // When confidence_level is set, use confidence-based borders
  if (confidenceLevel && confidenceLevel in HIGHLIGHT_BORDERS) {
    return (
      <div
        className={cn(HIGHLIGHT_BORDERS[confidenceLevel], className)}
        {...interactiveProps}
      >
        {children}
      </div>
    )
  }

  // Fallback: binary behavior
  const isUnverified = sourceSegmentIds.length === 0

  if (!isUnverified) {
    return <div className={className} {...interactiveProps}>{children}</div>
  }

  return (
    <div
      className={cn(
        "border-l-2 border-yellow-400 bg-yellow-50/50 pl-3",
        className,
      )}
      {...interactiveProps}
    >
      {children}
    </div>
  )
}
