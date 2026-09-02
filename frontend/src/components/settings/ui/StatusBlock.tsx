// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { LucideIcon } from "lucide-react"
import type { ReactNode } from "react"

type Tone = "sage" | "honey" | "sky" | "mute"

const TONES: Record<Tone, string> = {
  sage: "bg-secondary-400/20 text-secondary-600",
  honey: "bg-primary-500/20 text-primary-700",
  sky: "bg-accent-300/25 text-accent-500",
  mute: "bg-foreground/10 text-muted-foreground",
}

interface StatusBlockProps {
  icon: LucideIcon
  tone?: Tone
  title: ReactNode
  description?: ReactNode
  children?: ReactNode
}

/** "Here is where this connection stands", with the actions below it. */
export function StatusBlock({ icon: Icon, tone = "sage", title, description, children }: StatusBlockProps) {
  return (
    <div className="flex items-start gap-3">
      <div className={`grid h-9 w-9 shrink-0 place-items-center rounded-[10px] ${TONES[tone]}`}>
        <Icon className="h-[18px] w-[18px]" aria-hidden="true" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold text-foreground">{title}</div>
        {description && <div className="mt-0.5 text-[13px] leading-relaxed text-muted-foreground">{description}</div>}
        {children}
      </div>
    </div>
  )
}
