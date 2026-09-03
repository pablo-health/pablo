// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

type Tone = "sage" | "honey" | "sky" | "mute"

const TONES: Record<Tone, string> = {
  sage: "bg-secondary-400/20 text-secondary-600",
  honey: "bg-primary-500/20 text-primary-700",
  sky: "bg-accent-300/25 text-accent-500",
  mute: "bg-foreground/10 text-muted-foreground",
}

export function SettingsBadge({
  tone = "mute",
  className,
  children,
}: {
  tone?: Tone
  className?: string
  children: ReactNode
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-[3px] text-[11px] font-bold uppercase tracking-[0.06em]",
        TONES[tone],
        className
      )}
    >
      {children}
    </span>
  )
}
