// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

interface SettingsRowProps {
  label: ReactNode
  /** Only where it adds a consequence or a constraint; never a restated label. */
  description?: ReactNode
  /** A child of the row above it: indented and faintly tinted. */
  nested?: boolean
  children?: ReactNode
  className?: string
}

/**
 * The workhorse of the settings surface: a label on the left, its control on
 * the right, separated from the row above by a hairline.
 */
export function SettingsRow({ label, description, nested, children, className }: SettingsRowProps) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-5 border-t border-border px-[22px] py-3.5 first:border-t-0",
        nested && "bg-foreground/[0.025] pl-[46px]",
        className
      )}
    >
      <div className="min-w-0">
        <div className="text-sm font-semibold text-foreground">{label}</div>
        {description && (
          <div className="mt-0.5 text-[12.5px] leading-relaxed text-muted-foreground">{description}</div>
        )}
      </div>
      {children && <div className="flex shrink-0 items-center gap-2.5">{children}</div>}
    </div>
  )
}
