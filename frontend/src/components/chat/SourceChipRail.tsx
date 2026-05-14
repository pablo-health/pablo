// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Horizontal source-chip rail (§13.2). One chip per source in the
 * current per-turn selection, plus a trailing "+ Add source" affordance
 * for keys not in the selection. Opening the detail dialog is delegated
 * to the parent panel so a single dialog instance covers every chip.
 *
 * Stub sources reported as ``module_not_available`` in the latest
 * manifest are filtered out of the "+ Add" menu (you can't add what
 * isn't available).
 */

import { useState } from "react"
import { Plus } from "lucide-react"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  ContextManifest,
  SourceKey,
  SourceSelection,
  SOURCE_KEYS,
} from "@/lib/chat/types"
import { SOURCE_META } from "@/lib/chat/sourceMeta"

import { SourceChip } from "./SourceChip"

interface SourceChipRailProps {
  selection: SourceSelection
  latestManifest: ContextManifest | null
  onToggle: (key: SourceKey) => void
  onOpenDetail: (key: SourceKey) => void
  onAdd: (key: SourceKey) => void
}

export function SourceChipRail({
  selection,
  latestManifest,
  onToggle,
  onOpenDetail,
  onAdd,
}: SourceChipRailProps) {
  const activeKeys = SOURCE_KEYS.filter((key) => selection[key])
  const unavailableKeys = new Set(
    (latestManifest?.sources_dropped ?? [])
      .filter((entry) => entry.reason === "module_not_available")
      .map((entry) => entry.source_key),
  )
  const addableKeys = SOURCE_KEYS.filter(
    (key) => !selection[key] && !unavailableKeys.has(key),
  )

  return (
    <div
      data-slot="chat-source-rail"
      className="flex flex-wrap items-center gap-1.5"
    >
      {activeKeys.map((key) => (
        <SourceChip
          key={key}
          sourceKey={key}
          active
          secondary={secondaryLabelFor(key, latestManifest)}
          onToggle={onToggle}
          onOpenDetail={onOpenDetail}
        />
      ))}
      {addableKeys.length > 0 ? (
        <AddSourceButton addable={addableKeys} onAdd={onAdd} />
      ) : null}
    </div>
  )
}

function secondaryLabelFor(
  key: SourceKey,
  manifest: ContextManifest | null,
): string | undefined {
  if (!manifest) return undefined
  const entry = manifest.sources_included.find((e) => e.source_key === key)
  if (!entry) return undefined
  if (typeof entry.row_count === "number" && entry.row_count > 0) {
    return `${entry.row_count} ${entry.row_count === 1 ? "item" : "items"}`
  }
  if (typeof entry.chars === "number") {
    return `${entry.chars.toLocaleString()} chars`
  }
  return undefined
}

// ---------------------------------------------------------------------------
// + Add source affordance
// ---------------------------------------------------------------------------

function AddSourceButton({
  addable,
  onAdd,
}: {
  addable: SourceKey[]
  onAdd: (key: SourceKey) => void
}) {
  const [open, setOpen] = useState(false)

  return (
    <div className="relative">
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setOpen((prev) => !prev)}
        className={cn(
          "h-7 px-2 rounded-full border-dashed border-neutral-300 text-xs text-neutral-600",
          "hover:border-neutral-400 hover:text-neutral-800",
        )}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <Plus className="size-3" />
        Add source
      </Button>
      {open ? (
        <>
          {/* click-away */}
          <button
            type="button"
            aria-hidden="true"
            tabIndex={-1}
            className="fixed inset-0 cursor-default"
            onClick={() => setOpen(false)}
          />
          <div
            role="menu"
            data-slot="chat-add-source-menu"
            className={cn(
              "absolute z-10 mt-1 min-w-[220px] rounded-md border border-neutral-200 bg-white shadow-md p-1",
            )}
          >
            {addable.map((key) => {
              const meta = SOURCE_META[key]
              const Icon = meta.icon
              return (
                <button
                  key={key}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    onAdd(key)
                    setOpen(false)
                  }}
                  className={cn(
                    "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm",
                    "hover:bg-neutral-100 text-neutral-800",
                  )}
                >
                  <Icon className="size-3.5 text-neutral-500 shrink-0" />
                  <span className="flex-1 min-w-0">
                    <span className="block font-medium">{meta.label}</span>
                    <span className="block text-[11px] text-neutral-500 truncate">
                      {meta.description}
                    </span>
                  </span>
                </button>
              )
            })}
          </div>
        </>
      ) : null}
    </div>
  )
}
