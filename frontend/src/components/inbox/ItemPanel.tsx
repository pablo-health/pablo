// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import type { ReactNode } from "react"
import { formatInUserTimeZone, useUserTimeZone } from "@/hooks/usePreferences"
import type { InboxItem } from "@/lib/api/inbox"

export const WHEN: Intl.DateTimeFormatOptions = {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
}

/** The shared frame of an item's detail: whose it is, what it is, then the kind's own body. */
export function ItemPanel({ item, children }: { item: InboxItem; children?: ReactNode }) {
  const timeZone = useUserTimeZone()
  return (
    <div className="space-y-4 p-4" data-testid={`inbox-item-${item.kind}`}>
      <header>
        {item.patient_name && (
          <h2 className="text-lg font-semibold text-neutral-900">{item.patient_name}</h2>
        )}
        <p className="text-sm text-neutral-700">{item.title}</p>
        <p className="text-xs text-neutral-500">
          {formatInUserTimeZone(item.occurred_at, timeZone, WHEN)}
        </p>
      </header>
      {children}
    </div>
  )
}

/** Where the item is actually handled. The Inbox links there rather than re-doing it. */
export function OpenLink({ href, label }: { href: string; label: string }) {
  return (
    <Link
      href={href}
      className="inline-flex rounded-lg bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700"
      data-testid="inbox-open-link"
    >
      {label}
    </Link>
  )
}
