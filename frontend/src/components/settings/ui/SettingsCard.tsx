// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { useId } from "react"
import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

interface SettingsCardProps {
  title?: string
  description?: ReactNode
  /** Skip the body padding so the card can hold flush rows edge to edge. */
  flush?: boolean
  children: ReactNode
  className?: string
}

/**
 * The settings surface's one container. A white panel with an optional head.
 *
 * Deliberately not the global `.card` utility: that one carries its own padding
 * and a hover lift, both wrong for a static settings panel holding flush rows.
 */
export function SettingsCard({ title, description, flush, children, className }: SettingsCardProps) {
  const titleId = useId()

  return (
    <section
      aria-labelledby={title ? titleId : undefined}
      className={cn("mb-[18px] overflow-hidden rounded-2xl border border-border bg-card shadow-sm", className)}
    >
      {title && (
        <div className="px-[22px] pt-[18px]">
          <h2 id={titleId} className="font-display text-[17px] font-bold tracking-[-0.005em] text-foreground">
            {title}
          </h2>
          {description && <p className="mt-1 text-[13px] leading-relaxed text-muted-foreground">{description}</p>}
        </div>
      )}
      {flush ? children : <div className={cn("px-[22px] pb-5", title ? "pt-3.5" : "pt-2")}>{children}</div>}
    </section>
  )
}
