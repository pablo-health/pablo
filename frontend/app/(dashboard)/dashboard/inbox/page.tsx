// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Suspense } from "react"
import { Inbox } from "@/components/inbox/Inbox"

export default function InboxPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-display font-semibold text-neutral-900">Inbox</h1>
        <p className="mt-1 text-sm text-neutral-600">Everything waiting on you, in one place.</p>
      </div>
      {/* The filter, view and open item are read from the URL. */}
      <Suspense fallback={null}>
        <Inbox />
      </Suspense>
    </div>
  )
}
