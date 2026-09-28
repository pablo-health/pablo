// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Refills in the patient portal: see the ones already asked for, and ask
 * for another.
 *
 * Hangs off the patient session token it is handed, as messaging does.
 *
 * The requests come first. The form sits behind a "Request a refill"
 * button, except when there are no requests yet — then there is nothing
 * else to look at, so it starts open. If the requests cannot be loaded the
 * form is open too, since asking still works.
 *
 * A new request is put at the top of the list from the server's own
 * response — which carries the real id, status and timestamp, so nothing
 * here is guessed — and the list is then refetched so it settles on what
 * the store holds. The form closes, a one-line confirmation sits above the
 * list and the new row is marked, so the patient sees where the answer
 * will appear.
 *
 * The practice's name and whether messaging is on come from the portal's
 * capabilities. That lookup is best-effort: without it the confirmation
 * says "your practice" and the declined row leaves out the line that
 * points at messaging.
 *
 * The medication list failing to load does not block the form: the typed
 * name is always a valid way to ask, so the form falls back to it.
 */

"use client"

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import {
  createRefillRequest,
  listRefillMedications,
  listRefillRequests,
  type CreateRefillRequestInput,
  type RefillRequestList as RefillRequestListData,
} from "@/lib/api/patientRefills"
import { fetchCapabilities } from "@/lib/portal-shell/api"
import { RefillRequestForm } from "./RefillRequestForm"
import { RefillRequestList } from "./RefillRequestList"
import {
  LOAD_FAILED,
  OPEN_FORM,
  PRACTICE_FALLBACK,
  SUBMIT_FAILED,
  sentConfirmation,
} from "./refillsCopy"

const keys = {
  medications: (token: string) => ["patient-refills", "medications", token] as const,
  requests: (token: string) => ["patient-refills", "requests", token] as const,
  capabilities: (token: string) => ["portal-capabilities", token] as const,
}

export interface PortalRefillsProps {
  sessionToken: string
}

export function PortalRefills({ sessionToken }: PortalRefillsProps) {
  const queryClient = useQueryClient()
  const [formOpened, setFormOpened] = useState(false)
  const [sentId, setSentId] = useState<string | null>(null)

  const medications = useQuery({
    queryKey: keys.medications(sessionToken),
    queryFn: () => listRefillMedications(sessionToken),
  })

  const requests = useQuery({
    queryKey: keys.requests(sessionToken),
    queryFn: () => listRefillRequests(sessionToken),
  })

  const capabilities = useQuery({
    queryKey: keys.capabilities(sessionToken),
    queryFn: () => fetchCapabilities(sessionToken),
    staleTime: Infinity,
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
      setFormOpened(false)
      setSentId(created.id)
    },
  })

  if (medications.isPending) {
    return (
      <p data-testid="portal-refills-loading" className="text-sm text-neutral-600">
        Loading…
      </p>
    )
  }

  const portal = capabilities.data?.ok ? capabilities.data.data : null
  const practiceName = portal?.practice.display_name || PRACTICE_FALLBACK
  const messagingEnabled = portal?.modules.messaging === true

  const hasNone = requests.isError || requests.data?.data.length === 0
  const formOpen = formOpened || hasNone

  function openForm() {
    setSentId(null)
    setFormOpened(true)
  }

  return (
    <div className="flex flex-col gap-6" data-testid="portal-refills">
      {requests.isPending ? null : formOpen ? (
        <RefillRequestForm
          medications={medications.data?.data ?? []}
          onSubmit={(input) => create.mutateAsync(input)}
          submitting={create.isPending}
          error={create.isError ? SUBMIT_FAILED : null}
          onCancel={hasNone ? undefined : () => setFormOpened(false)}
        />
      ) : (
        <div>
          <Button type="button" data-testid="portal-refills-open-form" onClick={openForm}>
            {OPEN_FORM}
          </Button>
        </div>
      )}
      {requests.isError ? (
        <p data-testid="portal-refills-load-error" className="text-sm text-neutral-600">
          {LOAD_FAILED}
        </p>
      ) : requests.data ? (
        <RefillRequestList
          requests={requests.data.data}
          highlightId={sentId}
          messagingEnabled={messagingEnabled}
          notice={
            sentId ? (
              <p
                role="status"
                data-testid="portal-refills-sent"
                className="text-sm text-neutral-700"
              >
                {sentConfirmation(practiceName)}
              </p>
            ) : null
          }
        />
      ) : (
        <p data-testid="portal-refills-list-loading" className="text-sm text-neutral-600">
          Loading…
        </p>
      )}
    </div>
  )
}
