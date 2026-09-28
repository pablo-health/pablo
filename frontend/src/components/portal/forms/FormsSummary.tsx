// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The forms tile's line on the portal home screen: how many forms are still
 * waiting on the patient.
 *
 * Reads the same assignment list, under the same query key, as the forms
 * section — so opening the section draws from what the tile just fetched.
 * "To complete" counts the forms a patient can still write to, which is the
 * server's own status on each row; nothing here guesses at progress.
 *
 * Renders nothing while loading or on a failed fetch: the tile shows its
 * label alone, and the section is where a failure gets explained.
 */

"use client"

import { useQuery } from "@tanstack/react-query"
import { listAssignments } from "@/lib/api/patientIntake"
import { OPEN_STATUSES } from "./AssignmentList"
import { keys } from "./PortalForms"

export function formsSummaryLine(open: number): string {
  if (open === 0) return "Nothing to fill in"
  return open === 1 ? "1 form to complete" : `${open} forms to complete`
}

export function FormsSummary({ sessionToken }: { sessionToken: string }) {
  const assignments = useQuery({
    queryKey: keys.assignments(sessionToken),
    queryFn: () => listAssignments(sessionToken),
    retry: false,
  })
  if (!assignments.data) return null
  const open = assignments.data.filter((assignment) => OPEN_STATUSES.has(assignment.status))
  return <>{formsSummaryLine(open.length)}</>
}
