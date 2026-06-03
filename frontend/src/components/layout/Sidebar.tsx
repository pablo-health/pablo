// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { AdminNav } from "./AdminNav"
import { PabloNote } from "./PabloNote"
import { SidebarFooter } from "./SidebarFooter"
import { clinicianNavigation, settingsItem } from "./sidebarExtensions"

interface SidebarProps {
  isAdmin?: boolean
  /**
   * Hide the clinician nav items, leaving only Settings. For deployments where
   * an admin without a practice can't use the tenant-scoped clinician routes.
   * Honoured here; consumed by the SaaS overlay via `sidebarExtensions.ts`.
   */
  hideClinicianMenus?: boolean
}

export function Sidebar({ isAdmin = false, hideClinicianMenus = false }: SidebarProps) {
  const pathname = usePathname()

  const items = hideClinicianMenus
    ? [settingsItem]
    : [...clinicianNavigation, settingsItem]

  return (
    <div className="flex h-full w-64 flex-col bg-card border-r border-neutral-200">
      <div className="flex h-16 items-center px-6 border-b border-neutral-200">
        <h1 className="text-xl font-display font-bold text-primary-600">
          Pablo
        </h1>
      </div>

      <nav aria-label="Main navigation" className="flex-1 space-y-1 px-3 py-4">
        {items.map((item) => {
          const isActive = pathname === item.href
          return (
            <Link
              key={item.name}
              href={item.href}
              aria-current={isActive ? "page" : undefined}
              className={`
                group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium
                transition-all duration-200
                ${isActive
                  ? "bg-primary-50 text-primary-700 shadow-sm"
                  : "text-neutral-700 hover:bg-neutral-100 hover:text-neutral-900"
                }
              `}
            >
              <item.icon className={`h-5 w-5 transition-transform duration-200 ${isActive ? "" : "group-hover:scale-110"}`} />
              {item.name}
            </Link>
          )
        })}

        {isAdmin && <AdminNav />}
      </nav>

      <PabloNote />
      <SidebarFooter />
    </div>
  )
}
