// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Forms in the patient portal: what you were asked for, and filling one in.
 *
 * The whole surface hangs off the patient session token the shell hands it.
 * Nothing here goes looking for a signed-in user, because on this surface
 * there isn't one.
 *
 * Two fetches, and they answer different questions. The assignment list is
 * the practice's: which forms this patient was sent and how far each has
 * got. The intake form response is the engine's: the patient's own identity
 * fields, the "what brings you in" prompt, and each measure's published
 * items and anchors — the wording of the questions the engine asks itself,
 * fetched so the portal and the scorer cannot drift.
 *
 * That second fetch is allowed to fail quietly. A deployment that does not
 * answer it leaves the questions that need it saying so, and the rest of the
 * form still works.
 */

"use client"

import { useCallback, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  fetchIntakeForm,
  listAssignments,
  PatientIntakeError,
} from "@/lib/api/patientIntake"
import { AssignmentList } from "./AssignmentList"
import { FormsExpired, FormsLoadFailed, FormsLoading } from "./FormsNotice"
import { PacketFlow } from "./PacketFlow"

const keys = {
  assignments: (token: string) => ["patient-intake", "assignments", token] as const,
  form: (token: string) => ["patient-intake", "form", token] as const,
}

export interface PortalFormsProps {
  /** The live portal session token. This module never goes looking for one. */
  sessionToken: string
}

export function PortalForms({ sessionToken }: PortalFormsProps) {
  const queryClient = useQueryClient()
  const [openId, setOpenId] = useState<string | null>(null)
  const [sessionLost, setSessionLost] = useState(false)

  const assignments = useQuery({
    queryKey: keys.assignments(sessionToken),
    queryFn: () => listAssignments(sessionToken),
    retry: false,
  })

  const form = useQuery({
    queryKey: keys.form(sessionToken),
    queryFn: () => fetchIntakeForm(sessionToken),
    retry: false,
  })

  const refetchList = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: keys.assignments(sessionToken) })
  }, [queryClient, sessionToken])

  const expired =
    sessionLost ||
    (assignments.error instanceof PatientIntakeError && assignments.error.kind === "expired")

  if (expired) return <FormsExpired />
  if (assignments.isPending) return <FormsLoading />
  if (assignments.isError) {
    return <FormsLoadFailed onRetry={() => void assignments.refetch()} />
  }

  if (openId !== null) {
    return (
      <PacketFlow
        sessionToken={sessionToken}
        assignmentId={openId}
        form={form.data ?? null}
        onSessionLost={() => setSessionLost(true)}
        onChanged={refetchList}
        onClose={() => setOpenId(null)}
      />
    )
  }

  return <AssignmentList assignments={assignments.data} onOpen={setOpenId} />
}
