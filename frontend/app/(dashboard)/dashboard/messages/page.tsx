// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { MessagesInbox } from "@/components/messages/MessagesInbox"

export default function MessagesPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-display font-semibold text-neutral-900">Messages</h1>
        <p className="mt-1 text-sm text-neutral-600">What your clients wrote through the portal.</p>
      </div>
      <MessagesInbox />
    </div>
  )
}
