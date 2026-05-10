// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { AppointmentResponse } from "@/types/scheduling"

export interface LaneAssignment {
  appointment: AppointmentResponse
  lane: number
  laneCount: number
}

/**
 * Greedy lane-assignment for overlapping events in a single day column.
 *
 * Sort by start; for each event, pick the lowest lane index whose last
 * occupant has already ended. Cluster lane counts get back-propagated so
 * widths line up cleanly.
 */
export function assignLanes(items: AppointmentResponse[]): LaneAssignment[] {
  const sorted = [...items].sort(
    (a, b) => new Date(a.start_at).getTime() - new Date(b.start_at).getTime(),
  )

  const lanes: { end: number }[] = []
  const result: { appointment: AppointmentResponse; lane: number; clusterIdx: number }[] = []
  const clusters: number[][] = []
  let cluster: number[] = []
  let clusterEnd = -Infinity

  sorted.forEach((appt, idx) => {
    const start = new Date(appt.start_at).getTime()
    const end = new Date(appt.end_at).getTime()

    if (start >= clusterEnd) {
      if (cluster.length) clusters.push(cluster)
      cluster = []
      lanes.length = 0
      clusterEnd = end
    } else {
      clusterEnd = Math.max(clusterEnd, end)
    }

    let lane = lanes.findIndex((l) => l.end <= start)
    if (lane === -1) {
      lane = lanes.length
      lanes.push({ end })
    } else {
      lanes[lane] = { end }
    }
    cluster.push(idx)
    result.push({ appointment: appt, lane, clusterIdx: clusters.length })
  })
  if (cluster.length) clusters.push(cluster)

  return result.map((r) => {
    const clusterIndices = clusters[r.clusterIdx] ?? []
    const laneCount = clusterIndices.reduce(
      (max, i) => Math.max(max, result[i].lane + 1),
      1,
    )
    return { appointment: r.appointment, lane: r.lane, laneCount }
  })
}
