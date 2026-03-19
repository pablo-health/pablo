// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  Building2,
  Calendar,
  Home,
  Users,
  FileText,
  Settings,
  ShieldCheck,
  UserCog,
} from "lucide-react"

const navigation = [
  { name: "Dashboard", href: "/dashboard", icon: Home },
  { name: "Calendar", href: "/dashboard/calendar", icon: Calendar },
  { name: "Patients", href: "/dashboard/patients", icon: Users },
  { name: "Sessions", href: "/dashboard/sessions", icon: FileText },
  { name: "Export Review", href: "/dashboard/admin/export-review", icon: ShieldCheck },
  { name: "User Management", href: "/dashboard/admin/users", icon: UserCog },
  { name: "Tenants", href: "/dashboard/admin/tenants", icon: Building2 },
  { name: "Settings", href: "/dashboard/settings", icon: Settings },
]

export function Sidebar() {
  const pathname = usePathname()

  return (
    <div className="flex h-full w-64 flex-col bg-white border-r border-neutral-200">
      <div className="flex h-16 items-center px-6 border-b border-neutral-200">
        <h1 className="text-xl font-display font-bold text-primary-600">
          Pablo
        </h1>
      </div>

      <nav className="flex-1 space-y-1 px-3 py-4">
        {navigation.map((item) => {
          const isActive = pathname === item.href
          return (
            <Link
              key={item.name}
              href={item.href}
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
      </nav>

      <div className="p-4 border-t border-neutral-200">
        <div className="text-xs text-neutral-500">
          HIPAA Compliant Platform
        </div>
      </div>
    </div>
  )
}
