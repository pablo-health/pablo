// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One source pill in the chip rail (§13.2). Color-banded left edge,
 * lucide icon, label + secondary metadata, click to toggle, dedicated
 * detail button (caret) for the popover.
 */

import { ChevronRight } from "lucide-react"

import { cn } from "@/lib/utils"
import type { SourceKey } from "@/lib/chat/types"
import { FAMILY_STYLES, SOURCE_META } from "@/lib/chat/sourceMeta"

export interface SourceChipProps {
  sourceKey: SourceKey
  /** Currently included in the selection for the next turn. */
  active: boolean
  /** Optional secondary line, e.g. "5 notes · last May 9". */
  secondary?: string
  /** Disable both toggle and detail (e.g. stub sources). */
  disabled?: boolean
  onToggle: (key: SourceKey) => void
  onOpenDetail: (key: SourceKey) => void
}

export function SourceChip({
  sourceKey,
  active,
  secondary,
  disabled = false,
  onToggle,
  onOpenDetail,
}: SourceChipProps) {
  const meta = SOURCE_META[sourceKey]
  const styles = FAMILY_STYLES[meta.family]
  const Icon = meta.icon

  return (
    <span
      data-slot="chat-source-chip"
      data-source-key={sourceKey}
      data-active={active}
      className={cn(
        "inline-flex items-stretch overflow-hidden rounded-full border border-l-2 transition-colors",
        styles.border,
        active
          ? cn(styles.activeBg, styles.activeText, "border-neutral-300")
          : cn("bg-white", styles.inactiveText, "border-neutral-300"),
        disabled && "opacity-50",
      )}
    >
      <button
        type="button"
        onClick={() => !disabled && onToggle(sourceKey)}
        disabled={disabled}
        aria-pressed={active}
        className={cn(
          "flex items-center gap-2 pl-2.5 pr-1.5 py-1 text-xs font-medium",
          !disabled && "hover:bg-black/[0.03] cursor-pointer",
        )}
      >
        <Icon className="size-3.5 shrink-0" aria-hidden="true" />
        <span>{meta.label}</span>
        {secondary ? (
          <span className="text-[10px] text-neutral-500 font-normal whitespace-nowrap">
            {secondary}
          </span>
        ) : null}
      </button>
      <button
        type="button"
        onClick={() => !disabled && onOpenDetail(sourceKey)}
        disabled={disabled}
        aria-label={`Details for ${meta.label}`}
        className={cn(
          "flex items-center px-1.5 border-l border-neutral-200 text-neutral-500",
          !disabled && "hover:bg-black/[0.03] hover:text-neutral-700 cursor-pointer",
        )}
      >
        <ChevronRight className="size-3" />
      </button>
    </span>
  )
}
