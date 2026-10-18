// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The homework tile's line on the portal home screen: "2 to do", "All done",
 * or "Nothing assigned".
 *
 * Reads the list under the homework section's own query key, so opening the
 * section draws from what the tile just fetched. Renders nothing while
 * loading or on a failed fetch.
 */

"use client"

import { useQuery } from "@tanstack/react-query"
import { listHomework } from "@/lib/api/patientHomework"
import { summaryLine } from "./homeworkCopy"
import { homeworkKey } from "./PortalHomework"

export function HomeworkSummary({ sessionToken }: { sessionToken: string }) {
  const homework = useQuery({
    queryKey: homeworkKey(sessionToken),
    queryFn: () => listHomework(sessionToken),
  })
  if (!homework.data) return null
  const items = homework.data.data
  const done = items.filter((i) => i.status === "done").length
  return <>{summaryLine(items.length - done, done)}</>
}
