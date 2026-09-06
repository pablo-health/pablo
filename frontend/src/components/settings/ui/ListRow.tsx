// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { LucideIcon } from "lucide-react"
import type { ReactNode } from "react"

interface ListRowProps {
  icon?: LucideIcon
  title: ReactNode
  subtitle?: ReactNode
  children?: ReactNode
}

/** One entry in a list of things the practice owns: a passkey, a feed, a link. */
export function ListRow({ icon: Icon, title, subtitle, children }: ListRowProps) {
  return (
    <li className="flex items-center justify-between gap-3.5 border-t border-border py-3 first:border-t-0 first:pt-1">
      <div className="flex min-w-0 items-center gap-3">
        {Icon && (
          <div className="grid h-8 w-8 shrink-0 place-items-center rounded-[9px] bg-foreground/[0.08] text-muted-foreground">
            <Icon className="h-4 w-4" aria-hidden="true" />
          </div>
        )}
        <div className="min-w-0">
          <div className="text-sm font-semibold text-foreground">{title}</div>
          {subtitle && <div className="mt-0.5 text-[12.5px] text-muted-foreground">{subtitle}</div>}
        </div>
      </div>
      {children && <div className="flex shrink-0 items-center gap-1">{children}</div>}
    </li>
  )
}
