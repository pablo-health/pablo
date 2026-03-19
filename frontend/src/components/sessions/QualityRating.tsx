// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * QualityRating Component
 *
 * Display and edit 1-5 star quality ratings with visual feedback.
 * Supports interactive and readonly modes with keyboard navigation.
 */

"use client"

import { useState } from "react"
import { Star } from "lucide-react"
import { cn } from "@/lib/utils"

export interface QualityRatingProps {
  value: number | null
  onChange?: (rating: number) => void
  readonly?: boolean
  size?: "sm" | "md" | "lg"
  showLabel?: boolean
  className?: string
}

const sizeStyles = {
  sm: "w-4 h-4",
  md: "w-6 h-6",
  lg: "w-8 h-8",
}

export function QualityRating({
  value,
  onChange,
  readonly = false,
  size = "md",
  showLabel = false,
  className,
}: QualityRatingProps) {
  const [hoverRating, setHoverRating] = useState<number | null>(null)

  const isInteractive = !readonly && onChange !== undefined

  const handleClick = (rating: number) => {
    if (isInteractive) {
      onChange?.(rating)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent, rating: number) => {
    if (!isInteractive) return

    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault()
      onChange?.(rating)
    } else if (e.key === "ArrowRight" && rating < 5) {
      e.preventDefault()
      onChange?.(rating + 1)
    } else if (e.key === "ArrowLeft" && rating > 1) {
      e.preventDefault()
      onChange?.(rating - 1)
    }
  }

  const displayRating = hoverRating ?? value ?? 0

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div
        className="flex gap-1"
        role="group"
        aria-label="Quality rating"
        onMouseLeave={() => isInteractive && setHoverRating(null)}
      >
        {[1, 2, 3, 4, 5].map((rating) => {
          const isFilled = rating <= displayRating
          const isHovered = isInteractive && hoverRating === rating

          return (
            <button
              key={rating}
              type="button"
              onClick={() => handleClick(rating)}
              onMouseEnter={() => isInteractive && setHoverRating(rating)}
              onKeyDown={(e) => handleKeyDown(e, rating)}
              disabled={!isInteractive}
              className={cn(
                "transition-all",
                isInteractive && "cursor-pointer hover:scale-110",
                !isInteractive && "cursor-default",
                "focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 rounded"
              )}
              aria-label={`Rate ${rating} stars`}
              aria-pressed={value === rating}
              tabIndex={isInteractive ? 0 : -1}
            >
              <Star
                className={cn(
                  sizeStyles[size],
                  "transition-colors",
                  isFilled
                    ? "fill-amber-400 text-amber-400"
                    : "fill-none text-neutral-300",
                  isHovered && "fill-amber-300 text-amber-300"
                )}
              />
            </button>
          )
        })}
      </div>

      {showLabel && (
        <span
          className="text-sm text-neutral-600"
          aria-live="polite"
        >
          {value !== null ? `${value}/5` : "Not rated"}
        </span>
      )}
    </div>
  )
}
