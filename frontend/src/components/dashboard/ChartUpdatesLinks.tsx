// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Chart updates waiting, as Today shows them.
 *
 * A note proposes chart updates (an imported note, or one written in Pablo),
 * and they wait on the client's chart until the clinician accepts, edits or
 * discards each one. Nothing said they existed until the chart was opened.
 * Today names them in two quiet places: a line on a scheduled client's row,
 * linking to that chart (the waiting strip opens at the top of it), and one
 * line under the heading for every client with something waiting. Both are
 * absent when the count is zero; the counts come from the dashboard summary.
 */

"use client"

import Link from "next/link"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"

const updates = (count: number) => `${count} chart ${count === 1 ? "update" : "updates"}`

/** On an appointment row: this client's chart has updates to decide. */
export function ChartUpdatesWaitingLink({
  patientId,
  count,
}: {
  patientId: string
  count: number
}) {
  if (count <= 0) return null
  return (
    <Link
      href={`/dashboard/patients/${patientId}`}
      className="text-xs text-primary-700 hover:underline"
      data-testid="today-chart-updates"
    >
      {updates(count)} waiting
    </Link>
  )
}

/** Under the heading: how many clients, across the list, have any waiting. */
export function PeopleWithChartUpdates({ count }: { count: number }) {
  const people = usePeopleTerm()
  if (count <= 0) return null
  const who = count === 1 ? `1 ${people.one} has` : `${count} ${people.many} have`
  return (
    <p className="text-sm mt-1">
      <Link
        href="/dashboard/patients"
        className="text-primary-700 hover:underline"
        data-testid="today-chart-updates-total"
      >
        {who} chart updates waiting
      </Link>
    </p>
  )
}
