// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { RefillQueue } from "@/components/refills/RefillQueue"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"

export default function RefillsPage() {
  const people = usePeopleTerm()
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-display font-semibold text-neutral-900">Refill requests</h1>
        <p className="text-sm text-neutral-600 mt-1">
          What your {people.many} have asked for from the portal, oldest first.
        </p>
      </div>
      <RefillQueue />
    </div>
  )
}
