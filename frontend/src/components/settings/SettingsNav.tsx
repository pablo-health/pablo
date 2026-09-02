// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { Search } from "lucide-react"
import { useMemo, useState } from "react"
import { useFeatureGatePredicate } from "@/lib/featureGates"
import { settingsGroups } from "./registry"

const BASE = "/dashboard/settings"

/**
 * The settings nav: every group this build ships, minus whatever this account's
 * gates hide, filtered live by a search field.
 *
 * Search matches label, description and group name, because people look for
 * "recording" and "timezone", not for "Sessions" and "Profile".
 */
export function SettingsNav() {
  const pathname = usePathname()
  const allows = useFeatureGatePredicate()
  const [query, setQuery] = useState("")

  const active = pathname?.startsWith(`${BASE}/`) ? pathname.slice(BASE.length + 1).split("/")[0] : ""

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return settingsGroups
      .map((group) => ({
        ...group,
        items: group.items.filter((item) => {
          if (!allows(item.flag)) return false
          if (!needle) return true
          return `${item.label} ${item.desc} ${group.label}`.toLowerCase().includes(needle)
        }),
      }))
      .filter((group) => group.items.length > 0)
  }, [query, allows])

  return (
    <aside aria-label="Settings sections" className="flex min-h-0 flex-col border-r border-border bg-foreground/[0.03]">
      <div className="px-5 pb-3.5 pt-6">
        <h1 className="font-display text-[26px] font-bold leading-none tracking-[-0.01em] text-foreground">
          Settings
        </h1>
      </div>

      <div className="relative mx-3.5 mb-2.5">
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-[15px] w-[15px] -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Find a setting"
          aria-label="Find a setting"
          className="h-[38px] w-full rounded-[10px] border-[1.5px] border-border bg-card pl-9 pr-3 text-[13.5px] text-foreground outline-none transition-colors placeholder:text-muted-foreground focus:border-primary-500"
        />
      </div>

      <nav className="min-h-0 flex-1 overflow-y-auto px-3.5 pb-5 pt-1">
        {groups.length === 0 && (
          <p className="px-2.5 py-5 font-display text-[13px] italic text-muted-foreground">
            Nothing matches &ldquo;{query}&rdquo;.
          </p>
        )}

        {groups.map((group) => (
          <div key={group.id} className="mt-3.5">
            <div className="px-2.5 pb-1.5 text-[10.5px] font-bold uppercase tracking-[0.16em] text-muted-foreground/75">
              {group.label}
            </div>
            {group.items.map((item) => {
              const Icon = item.icon
              const isActive = active === item.id
              return (
                <Link
                  key={item.id}
                  href={`${BASE}/${item.id}`}
                  aria-current={isActive ? "page" : undefined}
                  className={[
                    "flex w-full items-center gap-2.5 rounded-[10px] px-2.5 py-2 text-[13.5px] transition-colors",
                    isActive
                      ? "bg-card font-semibold text-foreground shadow-sm ring-1 ring-inset ring-border"
                      : "font-medium text-muted-foreground hover:bg-foreground/[0.07] hover:text-foreground",
                  ].join(" ")}
                >
                  <Icon
                    className={`h-4 w-4 shrink-0 ${isActive ? "text-primary-600" : "opacity-80"}`}
                    aria-hidden="true"
                  />
                  {item.label}
                </Link>
              )
            })}
          </div>
        ))}
      </nav>
    </aside>
  )
}
