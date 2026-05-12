// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { HORIZONS, type HorizonId } from "./horizons"

interface HorizonStripProps {
  counts: Record<HorizonId, number>
  selected: HorizonId | "urgent" | "all"
  onSelect: (id: HorizonId | "urgent" | "all") => void
}

/**
 * Segmented control across compliance time horizons. The gradient under each
 * cell tightens from rose → amber → honey → sage → neutral, a visual proxy
 * for "how soon is this becoming a problem". Counts double as click targets.
 */
export function HorizonStrip({ counts, selected, onSelect }: HorizonStripProps) {
  const urgentCount = counts.overdue + counts.week + counts.month
  const totalVisible =
    urgentCount + counts.quarter + counts.beyond + counts.informational

  return (
    <div className="-mx-1 mb-4">
      <div className="flex items-stretch gap-1 px-1 py-1 rounded-xl bg-gradient-to-r from-rose-50 via-amber-50 to-emerald-50/60">
        <Tab
          label="Due now"
          sub={urgentCount === 1 ? "1 item" : `${urgentCount} items`}
          tone="urgent"
          active={selected === "urgent"}
          onClick={() => onSelect("urgent")}
        />
        {HORIZONS.filter((h) => h.id !== "informational").map((h) => (
          <Tab
            key={h.id}
            label={h.label}
            sub={`${counts[h.id]} · ${h.short}`}
            tone={h.id}
            active={selected === h.id}
            onClick={() => onSelect(h.id)}
          />
        ))}
        <Tab
          label="All"
          sub={`${totalVisible} total`}
          tone="all"
          active={selected === "all"}
          onClick={() => onSelect("all")}
        />
      </div>
    </div>
  )
}

const TONE_STYLES: Record<string, { idle: string; active: string; dot: string }> = {
  urgent: {
    idle: "hover:bg-white/60",
    active: "bg-white shadow-sm ring-1 ring-rose-200",
    dot: "bg-rose-500",
  },
  overdue: {
    idle: "hover:bg-white/60",
    active: "bg-white shadow-sm ring-1 ring-rose-200",
    dot: "bg-rose-500",
  },
  week: {
    idle: "hover:bg-white/60",
    active: "bg-white shadow-sm ring-1 ring-amber-300",
    dot: "bg-amber-500",
  },
  month: {
    idle: "hover:bg-white/60",
    active: "bg-white shadow-sm ring-1 ring-amber-200",
    dot: "bg-amber-400",
  },
  quarter: {
    idle: "hover:bg-white/60",
    active: "bg-white shadow-sm ring-1 ring-emerald-200",
    dot: "bg-emerald-400",
  },
  beyond: {
    idle: "hover:bg-white/60",
    active: "bg-white shadow-sm ring-1 ring-neutral-200",
    dot: "bg-neutral-400",
  },
  all: {
    idle: "hover:bg-white/60",
    active: "bg-white shadow-sm ring-1 ring-primary-200",
    dot: "bg-primary-500",
  },
}

function Tab({
  label,
  sub,
  tone,
  active,
  onClick,
}: {
  label: string
  sub: string
  tone: string
  active: boolean
  onClick: () => void
}) {
  const t = TONE_STYLES[tone] ?? TONE_STYLES.all
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`flex-1 min-w-0 rounded-lg px-2.5 py-2 text-left transition-all duration-200 ${
        active ? t.active : t.idle
      }`}
    >
      <div className="flex items-center gap-1.5">
        <span className={`h-1.5 w-1.5 rounded-full ${t.dot}`} aria-hidden />
        <span className="text-[11px] font-medium text-neutral-700 truncate">
          {label}
        </span>
      </div>
      <p className="text-[10px] text-neutral-500 mt-0.5 truncate">{sub}</p>
    </button>
  )
}
