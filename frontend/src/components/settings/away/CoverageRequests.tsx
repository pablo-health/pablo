// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Button } from "@/components/ui/button"
import { useAnswerCoverageRequest, useCoverageRequests } from "@/hooks/useAwayPeriods"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import type { CoverageRequest } from "@/types/away"
import { SettingsBadge, SettingsCard } from "../ui"
import { day } from "./day"

function RequestRow({ request }: { request: CoverageRequest }) {
  const answer = useAnswerCoverageRequest()
  const respond = (choice: "accept" | "decline") =>
    answer.mutate({ periodId: request.period_id, answer: choice })
  const accepted = request.status === "accepted"
  return (
    <li
      data-testid={`coverage-request-${request.period_id}`}
      className="flex flex-wrap items-center justify-between gap-3 border-t border-border py-3 first:border-t-0"
    >
      <div className="space-y-0.5">
        <p className="text-sm text-foreground">
          {request.clinician_name ?? "A colleague"}
          {accepted && <SettingsBadge className="ml-2">Covering</SettingsBadge>}
          {request.status === "declined" && (
            <SettingsBadge className="ml-2">Declined</SettingsBadge>
          )}
        </p>
        <p className="text-[12.5px] text-muted-foreground">
          Away {day(request.start_date)} – {day(request.end_date)} · Back{" "}
          {day(request.return_date)}
        </p>
        {answer.error && (
          <p className="text-[12.5px] text-red-700" role="alert">
            {answer.error.message}
          </p>
        )}
      </div>
      <div className="flex gap-2">
        {!accepted && (
          <Button
            size="sm"
            data-testid={`coverage-accept-${request.period_id}`}
            onClick={() => respond("accept")}
          >
            Accept
          </Button>
        )}
        {request.status !== "declined" && (
          <Button
            size="sm"
            variant="ghost"
            data-testid={`coverage-decline-${request.period_id}`}
            onClick={() => respond("decline")}
          >
            {accepted ? "Stop covering" : "Decline"}
          </Button>
        )}
      </div>
    </li>
  )
}

/**
 * Away dates a colleague asked the clinician to cover. Accepting opens the
 * colleague's clients' records for exactly those dates (app.away.coverage);
 * the card is absent when nobody has asked.
 */
export function CoverageRequests() {
  const { data } = useCoverageRequests()
  const people = usePeopleTerm()
  if (!data || data.requests.length === 0) return null
  return (
    <SettingsCard
      title="Covering for colleagues"
      description={`When you accept, you can open their ${people.many}' records on the dates they're away.`}
    >
      <ul data-testid="coverage-requests">
        {data.requests.map((request) => (
          <RequestRow key={request.period_id} request={request} />
        ))}
      </ul>
    </SettingsCard>
  )
}
