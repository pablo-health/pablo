// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Refills in the patient portal: ask for one, and see the ones already
 * asked for.
 *
 * Hangs off the patient session token it is handed, as messaging does.
 *
 * A new request is put at the top of the list from the server's own
 * response — which carries the real id, status and timestamp, so nothing
 * here is guessed — and the list is then refetched so it settles on what
 * the store holds.
 *
 * The medication list failing to load does not block the form: the typed
 * name is always a valid way to ask, so the form falls back to it.
 */

"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  createRefillRequest,
  listRefillMedications,
  listRefillRequests,
  type CreateRefillRequestInput,
  type RefillRequestList as RefillRequestListData,
} from "@/lib/api/patientRefills"
import { RefillRequestForm } from "./RefillRequestForm"
import { RefillRequestList } from "./RefillRequestList"
import { LOAD_FAILED, SUBMIT_FAILED } from "./refillsCopy"

const keys = {
  medications: (token: string) => ["patient-refills", "medications", token] as const,
  requests: (token: string) => ["patient-refills", "requests", token] as const,
}

export interface PortalRefillsProps {
  sessionToken: string
}

export function PortalRefills({ sessionToken }: PortalRefillsProps) {
  const queryClient = useQueryClient()

  const medications = useQuery({
    queryKey: keys.medications(sessionToken),
    queryFn: () => listRefillMedications(sessionToken),
  })

  const requests = useQuery({
    queryKey: keys.requests(sessionToken),
    queryFn: () => listRefillRequests(sessionToken),
  })

  const create = useMutation({
    mutationFn: (input: CreateRefillRequestInput) =>
      createRefillRequest(sessionToken, input),
    onSuccess: (created) => {
      queryClient.setQueryData<RefillRequestListData>(
        keys.requests(sessionToken),
        (current) =>
          current
            ? {
                data: [created, ...current.data.filter((r) => r.id !== created.id)],
                total: current.total + 1,
              }
            : { data: [created], total: 1 },
      )
      void queryClient.invalidateQueries({ queryKey: keys.requests(sessionToken) })
    },
  })

  if (medications.isPending) {
    return (
      <p data-testid="portal-refills-loading" className="text-sm text-neutral-600">
        Loading…
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-6" data-testid="portal-refills">
      <RefillRequestForm
        medications={medications.data?.data ?? []}
        onSubmit={(input) => create.mutateAsync(input)}
        submitting={create.isPending}
        error={create.isError ? SUBMIT_FAILED : null}
      />
      {requests.isError ? (
        <p data-testid="portal-refills-load-error" className="text-sm text-neutral-600">
          {LOAD_FAILED}
        </p>
      ) : requests.data ? (
        <RefillRequestList requests={requests.data.data} />
      ) : (
        <p data-testid="portal-refills-list-loading" className="text-sm text-neutral-600">
          Loading…
        </p>
      )}
    </div>
  )
}
