// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { RecordActivityItem } from "@/lib/api/patientActivity"

/**
 * How close together the same action by the same person has to be to read as
 * one line. Opening a chart writes a row for each request the page makes, so
 * one visit can leave several identical rows in the same second.
 */
export const ACTIVITY_GROUP_WINDOW_MS = 5_000

/** One line in Record activity: a row, or several identical ones close together. */
export interface ActivityGroup {
  /** The newest row's id: stable while older pages load. */
  id: string
  /** Newest first, exactly as the server sent them. */
  rows: RecordActivityItem[]
}

function sameAction(item: RecordActivityItem): string {
  return JSON.stringify([
    item.actor_type,
    item.actor_name,
    item.is_you,
    item.action,
    item.resource_type,
  ])
}

/**
 * Fold identical rows that land within `windowMs` of each other into one line.
 *
 * Display only: every row stays in its group, and the server's trail is
 * untouched. Rows come newest first, and a group is measured from its newest
 * row, so loading an older page can only add to the last groups, never
 * rearrange the lines already on screen. Another action in between does not
 * split a group: a page load interleaves its requests, and the window already
 * bounds how far apart the folded rows can be. A row whose time cannot be
 * read is never folded.
 */
export function groupRecordActivity(
  entries: RecordActivityItem[],
  windowMs: number = ACTIVITY_GROUP_WINDOW_MS
): ActivityGroup[] {
  const groups: ActivityGroup[] = []
  const open = new Map<string, { group: ActivityGroup; newest: number }>()

  for (const item of entries) {
    const at = Date.parse(item.timestamp)
    if (Number.isNaN(at)) {
      groups.push({ id: item.id, rows: [item] })
      continue
    }
    const key = sameAction(item)
    const current = open.get(key)
    if (current && current.newest - at <= windowMs && at <= current.newest) {
      current.group.rows.push(item)
      continue
    }
    const group = { id: item.id, rows: [item] }
    groups.push(group)
    open.set(key, { group, newest: at })
  }
  return groups
}
