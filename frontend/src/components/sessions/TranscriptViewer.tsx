// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * TranscriptViewer Component
 *
 * Display transcript with format-specific rendering, copy to clipboard, and expand/collapse.
 * When transcript_segments are available, renders individual segments with highlight support
 * for source-linking from SOAP claims.
 */

"use client"

import { useState, useRef, useEffect, useImperativeHandle, forwardRef, useCallback } from "react"
import { Copy, ChevronDown, ChevronUp, Check } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { formatTranscriptDisplay } from "@/lib/utils/transcriptParser"
import type { TranscriptModel, TranscriptSegment } from "@/types/sessions"

export interface TranscriptViewerHandle {
  scrollToSegment: (index: number) => void
}

export interface TranscriptViewerProps {
  transcript: TranscriptModel
  transcriptSegments?: TranscriptSegment[] | null
  highlightedSegments?: number[]
  className?: string
}

const COLLAPSED_MAX_HEIGHT = "12rem" // ~12 lines
const PREVIEW_CHARS = 500

function formatTimestamp(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, "0")}`
}

export const TranscriptViewer = forwardRef<TranscriptViewerHandle, TranscriptViewerProps>(
  function TranscriptViewer(
    { transcript, transcriptSegments, highlightedSegments = [], className },
    ref,
  ) {
    const [isExpanded, setIsExpanded] = useState(false)
    const [copiedRecently, setCopiedRecently] = useState(false)
    const containerRef = useRef<HTMLDivElement>(null)
    const segmentRefs = useRef<Map<number, HTMLDivElement>>(new Map())

    const formattedContent = formatTranscriptDisplay(transcript)
    const isLong = transcriptSegments
      ? transcriptSegments.length > 8
      : formattedContent.length > PREVIEW_CHARS

    const scrollToSegment = useCallback((index: number) => {
      // Auto-expand if collapsed
      if (!isExpanded && isLong) {
        setIsExpanded(true)
      }

      // Use requestAnimationFrame to ensure DOM updates after expansion
      requestAnimationFrame(() => {
        const el = segmentRefs.current.get(index)
        if (el) {
          el.scrollIntoView({ behavior: "smooth", block: "center" })
        }
      })
    }, [isExpanded, isLong])

    useImperativeHandle(ref, () => ({ scrollToSegment }), [scrollToSegment])

    const handleCopyToClipboard = async () => {
      try {
        await navigator.clipboard.writeText(formattedContent)
        setCopiedRecently(true)
        setTimeout(() => setCopiedRecently(false), 2000)
      } catch {
        console.error("Failed to copy to clipboard")
      }
    }

    const setSegmentRef = useCallback((index: number, el: HTMLDivElement | null) => {
      if (el) {
        segmentRefs.current.set(index, el)
      } else {
        segmentRefs.current.delete(index)
      }
    }, [])

    const hasSegments = transcriptSegments && transcriptSegments.length > 0

    return (
      <div className={cn("card space-y-4", className)}>
        {/* Header */}
        <div className="flex justify-between items-center">
          <div>
            <h3 className="text-lg font-semibold text-neutral-900">
              Transcript
            </h3>
            <p className="text-sm text-neutral-500">
              Format: {transcript.format.toUpperCase()}
            </p>
          </div>

          <Button
            variant="outline"
            size="sm"
            onClick={handleCopyToClipboard}
            aria-label="Copy transcript to clipboard"
          >
            {copiedRecently ? (
              <>
                <Check className="w-4 h-4 mr-2" />
                Copied
              </>
            ) : (
              <>
                <Copy className="w-4 h-4 mr-2" />
                Copy
              </>
            )}
          </Button>
        </div>

        {/* Content */}
        <div
          ref={containerRef}
          className={cn(
            "relative overflow-hidden transition-all duration-300",
            !isExpanded && isLong && "max-h-48"
          )}
          style={{ maxHeight: !isExpanded && isLong ? COLLAPSED_MAX_HEIGHT : undefined }}
        >
          {hasSegments ? (
            <div className="space-y-2" data-testid="segment-list">
              {transcriptSegments.map((seg) => {
                const isHighlighted = highlightedSegments.includes(seg.index)
                return (
                  <div
                    key={seg.index}
                    ref={(el) => setSegmentRef(seg.index, el)}
                    data-segment-index={seg.index}
                    className={cn(
                      "rounded px-3 py-2 transition-colors duration-300",
                      isHighlighted
                        ? "bg-blue-100 ring-1 ring-blue-300"
                        : "hover:bg-neutral-50",
                    )}
                  >
                    <div className="flex items-baseline gap-2">
                      <span className="text-xs text-neutral-400 font-mono shrink-0">
                        {formatTimestamp(seg.start_time)}
                      </span>
                      <span className="text-xs font-medium text-neutral-600 shrink-0">
                        {seg.speaker}:
                      </span>
                      <span className="text-sm text-neutral-700 leading-relaxed">
                        {seg.text}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <pre className="whitespace-pre-wrap text-sm text-neutral-700 font-mono leading-relaxed">
              {formattedContent}
            </pre>
          )}

          {/* Fade overlay when collapsed */}
          {!isExpanded && isLong && (
            <div className="absolute bottom-0 left-0 right-0 h-16 bg-gradient-to-t from-white to-transparent pointer-events-none" />
          )}
        </div>

        {/* Expand/Collapse Button */}
        {isLong && (
          <div className="flex justify-center pt-2 border-t border-neutral-200">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setIsExpanded(!isExpanded)}
              aria-label={isExpanded ? "Collapse transcript" : "Expand transcript"}
              aria-expanded={isExpanded}
            >
              {isExpanded ? (
                <>
                  <ChevronUp className="w-4 h-4 mr-2" />
                  Show Less
                </>
              ) : (
                <>
                  <ChevronDown className="w-4 h-4 mr-2" />
                  Show More
                </>
              )}
            </Button>
          </div>
        )}
      </div>
    )
  }
)
