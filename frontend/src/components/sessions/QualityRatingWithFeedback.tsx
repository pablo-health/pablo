// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * QualityRatingWithFeedback Component
 *
 * Extends QualityRating with conditional feedback collection:
 * - Shows section checkboxes when rating is below threshold
 * - Shows textarea for additional comments when rating is below threshold
 * - Threshold configured via runtime config (RATING_FEEDBACK_REQUIRED_BELOW)
 */

"use client"

import { useState, useEffect } from "react"
import { QualityRating } from "./QualityRating"
import { useConfig } from "@/lib/config"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import { Textarea } from "@/components/ui/textarea"

export interface RatingFeedback {
  rating: number | null
  reason: string
  sections: string[]
}

export interface QualityRatingWithFeedbackProps {
  value: RatingFeedback
  onChange: (feedback: RatingFeedback) => void
  readonly?: boolean
  className?: string
}

const SOAP_SECTIONS = [
  { id: "subjective", label: "Subjective" },
  { id: "objective", label: "Objective" },
  { id: "assessment", label: "Assessment" },
  { id: "plan", label: "Plan" },
]

export function QualityRatingWithFeedback({
  value,
  onChange,
  readonly = false,
  className,
}: QualityRatingWithFeedbackProps) {
  const config = useConfig()
  const [localValue, setLocalValue] = useState<RatingFeedback>(value)

  // Sync local state with prop changes
  useEffect(() => {
    setLocalValue(value)
  }, [value])

  const showFeedbackUI =
    localValue.rating !== null &&
    localValue.rating < config.ratingFeedbackRequiredBelow

  const handleRatingChange = (rating: number) => {
    const newValue: RatingFeedback = {
      ...localValue,
      rating,
      // Clear feedback if rating goes above threshold
      ...(rating >= config.ratingFeedbackRequiredBelow && {
        reason: "",
        sections: [],
      }),
    }
    setLocalValue(newValue)
    onChange(newValue)
  }

  const handleSectionToggle = (sectionId: string, checked: boolean) => {
    const newSections = checked
      ? [...localValue.sections, sectionId]
      : localValue.sections.filter((s) => s !== sectionId)

    const newValue: RatingFeedback = {
      ...localValue,
      sections: newSections,
    }
    setLocalValue(newValue)
    onChange(newValue)
  }

  const handleReasonChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newValue: RatingFeedback = {
      ...localValue,
      reason: e.target.value,
    }
    setLocalValue(newValue)
    onChange(newValue)
  }

  return (
    <div className={className}>
      {/* Quality Rating Stars */}
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-neutral-700">
          Quality Rating:
        </span>
        <QualityRating
          value={localValue.rating}
          onChange={handleRatingChange}
          readonly={readonly}
        />
      </div>

      {/* Feedback UI - shown when rating < threshold */}
      {showFeedbackUI && !readonly && (
        <div className="mt-4 space-y-4 p-4 border border-neutral-200 rounded-md bg-neutral-50">
          <div>
            <Label className="text-sm font-medium text-neutral-900 mb-2 block">
              Which sections need improvement?
            </Label>
            <div className="space-y-2">
              {SOAP_SECTIONS.map((section) => (
                <div key={section.id} className="flex items-center space-x-2">
                  <Checkbox
                    id={`section-${section.id}`}
                    checked={localValue.sections.includes(section.id)}
                    onCheckedChange={(checked) =>
                      handleSectionToggle(section.id, checked === true)
                    }
                  />
                  <label
                    htmlFor={`section-${section.id}`}
                    className="text-sm text-neutral-700 cursor-pointer"
                  >
                    {section.label}
                  </label>
                </div>
              ))}
            </div>
          </div>

          <div>
            <Label
              htmlFor="rating-reason"
              className="text-sm font-medium text-neutral-900 mb-2 block"
            >
              Additional feedback (optional)
            </Label>
            <Textarea
              id="rating-reason"
              value={localValue.reason}
              onChange={handleReasonChange}
              placeholder="What specifically needs improvement?"
              className="min-h-[100px] resize-none"
            />
          </div>
        </div>
      )}
    </div>
  )
}
