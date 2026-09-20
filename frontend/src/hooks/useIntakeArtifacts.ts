// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useMemo } from "react"
import { useQueries } from "@tanstack/react-query"

import {
  listIntakeArtifacts,
  listIntakeAssignments,
  type IntakeAssignment,
  type IntakeChartArtifact,
} from "@/lib/api/intakeReview"
import { useAuthQuery } from "./useAuthQuery"

/**
 * Cache keys for the chart's read of what a form collected.
 *
 * Declared here rather than in the shared factory for the reason the
 * review's are: nothing outside this hook reads or invalidates them yet.
 */
export const intakeArtifactKeys = {
  all: ["intakeArtifacts"] as const,
  assignments: (patientId: string) => [...intakeArtifactKeys.all, "assignments", patientId] as const,
  forAssignment: (patientId: string, assignmentId: string) =>
    [...intakeArtifactKeys.all, patientId, assignmentId] as const,
}

/** One form, and the files it collected. */
export interface IntakeArtifactGroup {
  assignment: IntakeAssignment
  artifacts: IntakeChartArtifact[]
}

/**
 * Every file on this patient's forms, grouped by the form that asked.
 *
 * Two rounds rather than one: the assignments list says which forms exist,
 * and the files are read per form because that is the granularity the
 * disclosure is recorded at. A form that collected nothing is dropped, so
 * a chart with no files renders nothing at all.
 *
 * `retry: false` throughout, like the card that reads this: every attempt
 * is an audited read, and a screen that hides itself on an error gains
 * nothing by asking three times first.
 */
export function useIntakeArtifacts(patientId: string | undefined, token?: string) {
  const assignments = useAuthQuery<IntakeAssignment[]>({
    queryKey: intakeArtifactKeys.assignments(patientId ?? ""),
    queryFn: () => listIntakeAssignments(patientId!, token),
    enabled: !!patientId,
    retry: false,
  })

  const rows = assignments.data ?? []
  const files = useQueries({
    queries: rows.map((assignment) => ({
      queryKey: intakeArtifactKeys.forAssignment(patientId ?? "", assignment.id),
      queryFn: () => listIntakeArtifacts(patientId!, assignment.id, token),
      enabled: !!patientId,
      retry: false,
    })),
  })

  // Memoised on what the queries actually resolved to: `useQueries`
  // returns a new array identity every render, so grouping without this
  // would hand a new array to the section on every keystroke elsewhere on
  // the chart.
  const signature = files.map((query) => query.dataUpdatedAt).join(",")
  const groups = useMemo<IntakeArtifactGroup[]>(
    () =>
      rows
        .map((assignment, index) => ({
          assignment,
          artifacts: files[index]?.data ?? [],
        }))
        .filter((group) => group.artifacts.length > 0),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed by resolution time, see above
    [assignments.dataUpdatedAt, signature],
  )

  return {
    groups,
    isLoading: assignments.isLoading || files.some((query) => query.isLoading),
    error: assignments.error,
  }
}
