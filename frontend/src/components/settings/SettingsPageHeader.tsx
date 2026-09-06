// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { usePathname } from "next/navigation"
import { findSettingsItem } from "./registry"

const BASE = "/dashboard/settings"

/** The group crumb, page title and one-line description above every page. */
export function SettingsPageHeader() {
  const pathname = usePathname()
  const section = pathname?.startsWith(`${BASE}/`) ? pathname.slice(BASE.length + 1).split("/")[0] : ""
  const item = findSettingsItem(section)

  if (!item) return <div />

  return (
    <div>
      <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.14em] text-primary-600">{item.groupLabel}</div>
      <h1 className="mb-1.5 font-display text-[30px] font-bold leading-[1.05] tracking-[-0.015em] text-foreground">
        {item.label}
      </h1>
      <p className="max-w-[540px] text-sm leading-relaxed text-muted-foreground">{item.desc}</p>
    </div>
  )
}
