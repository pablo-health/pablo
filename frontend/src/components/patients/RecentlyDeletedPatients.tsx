// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Recently Deleted Patients
 *
 * Lists soft-deleted patients still inside the 30-day undo window
 * (THERAPY-yg2) and exposes a per-row Restore action. Past-window rows
 * never appear here — the backend filter (`include_deleted=recent`)
 * drops them, and the day-30 hard-purge cron physically removes them
 * (THERAPY-cgy).
 *
 * UX notes (per THERAPY-nyb):
 *   * Use "deleted" while inside the window — never "deleted forever."
 *     "Permanently removed" is reserved for events emitted after the
 *     hard-purge cron runs.
 *   * Show "X days remaining" so the practitioner knows the undo
 *     deadline.
 *   * Do not surface session counts as "lost" — restoring brings the
 *     cascaded sessions/notes back together with their original
 *     `session_number`s preserved (THERAPY-nyb invariant).
 */

import { Undo2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useToast } from "@/components/ui/Toast"
import { usePatientList, useRestorePatient } from "@/hooks/usePatients"
import type { PatientResponse } from "@/types/patients"

const MS_PER_DAY = 1000 * 60 * 60 * 24

/**
 * Days between `now` and `deadlineIso`. Returns 0 if the deadline is
 * in the past so the UI never shows a negative countdown — past-window
 * rows shouldn't appear here at all, but this defends against clock
 * skew between client and server.
 */
function daysRemaining(deadlineIso: string | null): number {
  if (!deadlineIso) return 0
  const deadline = new Date(deadlineIso).getTime()
  if (Number.isNaN(deadline)) return 0
  const diff = deadline - Date.now()
  if (diff <= 0) return 0
  return Math.max(1, Math.ceil(diff / MS_PER_DAY))
}

function formatDeletedAt(deletedAtIso: string | null): string {
  if (!deletedAtIso) return "—"
  try {
    return new Date(deletedAtIso).toLocaleDateString()
  } catch {
    return "—"
  }
}

export function RecentlyDeletedPatients() {
  const { showToast } = useToast()
  const { data, isLoading, error } = usePatientList({
    include_deleted: "recent",
  })
  const restore = useRestorePatient()

  const patients: PatientResponse[] = data?.data ?? []

  const handleRestore = async (patient: PatientResponse) => {
    try {
      await restore.mutateAsync(patient.id)
      showToast(
        `Restored ${patient.first_name} ${patient.last_name}.`,
        "success",
      )
    } catch {
      // The auth-mutation hook surfaces API errors; we still want a
      // user-visible toast here so the action's outcome is obvious.
      showToast("Could not restore patient. Please try again.", "error")
    }
  }

  if (error) {
    return (
      <div className="card text-center py-12">
        <p className="text-red-500">
          Failed to load recently deleted patients. Please try again.
        </p>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="card text-center py-12">
        <p className="text-neutral-500">Loading recently deleted patients...</p>
      </div>
    )
  }

  if (patients.length === 0) {
    return (
      <div className="card text-center py-12">
        <p className="text-neutral-500">
          No recently deleted patients. Deleted patients can be restored here
          for 30 days; after that they are permanently removed.
        </p>
      </div>
    )
  }

  return (
    <div className="card">
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Sessions</TableHead>
              <TableHead>Deleted</TableHead>
              <TableHead>Time remaining</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {patients.map((patient) => {
              const remaining = daysRemaining(patient.restore_deadline)
              return (
                <TableRow key={patient.id}>
                  <TableCell className="font-medium">
                    {patient.first_name} {patient.last_name}
                  </TableCell>
                  <TableCell>{patient.session_count}</TableCell>
                  <TableCell>{formatDeletedAt(patient.deleted_at)}</TableCell>
                  <TableCell>
                    {remaining === 1
                      ? "1 day remaining"
                      : `${remaining} days remaining`}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`Restore patient ${patient.first_name} ${patient.last_name}`}
                      disabled={restore.isPending}
                      onClick={() => handleRestore(patient)}
                    >
                      <Undo2 className="w-4 h-4 mr-2" />
                      Restore
                    </Button>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
