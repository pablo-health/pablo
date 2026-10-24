// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { ApiError } from "@/lib/api/client"
import {
  useCreateAwayPeriod,
  usePreviewAcknowledgment,
  useUpdateAwayPeriod,
} from "@/hooks/useAwayPeriods"
import type { AwayDates, AwayPeriod, CoveringOption } from "@/types/away"
import { Toggle } from "../ui"

interface AwayPeriodFormProps {
  /** The period being changed; absent when setting new dates. */
  period?: AwayPeriod
  coveringOptions: CoveringOption[]
  onDone: () => void
}

const SAVE_FAILED = "These dates could not be saved."

function messageOf(error: unknown): string {
  return error instanceof ApiError && error.message ? error.message : SAVE_FAILED
}

/**
 * Away dates in two steps: the dates, then the reply clients will get.
 *
 * The reply is always reviewed against the dates it names. Moving to the
 * review step asks the API for a suggestion; when the dates or the covering
 * colleague have not changed, the reply already saved is kept instead, so an
 * edit to the wording is never thrown away. The safety lines are shown but
 * are not part of what can be edited — the API adds them to every reply.
 */
export function AwayPeriodForm({ period, coveringOptions, onDone }: AwayPeriodFormProps) {
  const [dates, setDates] = useState<AwayDates>({
    start_date: period?.start_date ?? "",
    end_date: period?.end_date ?? "",
    return_date: period?.return_date ?? "",
    covering_user_id: period?.covering_user_id ?? "",
  })
  const [step, setStep] = useState<"dates" | "review">("dates")
  const [reply, setReply] = useState(period?.acknowledgment_text ?? "")
  const [replyOn, setReplyOn] = useState(period?.acknowledgment_enabled ?? true)
  const [safetyLines, setSafetyLines] = useState(period?.safety_lines ?? "")
  const [problem, setProblem] = useState<string | null>(null)

  const preview = usePreviewAcknowledgment()
  const create = useCreateAwayPeriod()
  const update = useUpdateAwayPeriod()

  const sent: AwayDates = {
    start_date: dates.start_date,
    end_date: dates.end_date,
    return_date: dates.return_date || null,
    covering_user_id: dates.covering_user_id || null,
  }
  const unchanged =
    period !== undefined &&
    period.start_date === sent.start_date &&
    period.end_date === sent.end_date &&
    period.return_date === (sent.return_date ?? period.return_date) &&
    period.covering_user_id === sent.covering_user_id

  const toReview = () => {
    setProblem(null)
    if (!dates.start_date || !dates.end_date) {
      setProblem("Choose the first and last day you're away.")
      return
    }
    if (unchanged) {
      setStep("review")
      return
    }
    preview.mutate(sent, {
      onSuccess: (suggested) => {
        setReply(suggested.acknowledgment_text)
        setSafetyLines(suggested.safety_lines)
        setDates((d) => ({ ...d, return_date: suggested.return_date }))
        setStep("review")
      },
      onError: (error) => setProblem(messageOf(error)),
    })
  }

  const save = () => {
    setProblem(null)
    if (!reply.trim()) {
      setProblem("Write the reply clients will get.")
      return
    }
    const body = { ...sent, acknowledgment_enabled: replyOn, acknowledgment_text: reply }
    const handlers = { onSuccess: onDone, onError: (error: unknown) => setProblem(messageOf(error)) }
    if (period) update.mutate({ periodId: period.id, data: body }, handlers)
    else create.mutate(body, handlers)
  }

  const saving = create.isPending || update.isPending

  if (step === "review") {
    return (
      <div className="space-y-4" data-testid="away-review">
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm font-medium text-foreground">Reply to clients while you&apos;re away</p>
          <Toggle
            checked={replyOn}
            onChange={setReplyOn}
            label="Reply to clients while you're away"
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="away-reply">Reply</Label>
          <Textarea
            id="away-reply"
            data-testid="away-reply-text"
            rows={4}
            value={reply}
            onChange={(e) => setReply(e.target.value)}
          />
          <div
            data-testid="away-safety-lines"
            className="whitespace-pre-wrap rounded-md bg-muted px-3 py-2 text-[13px] text-muted-foreground"
          >
            {safetyLines}
          </div>
          <p className="text-[12.5px] text-muted-foreground">
            Each client gets this once per conversation while you&apos;re away.
          </p>
        </div>
        {problem && (
          <p className="text-[12.5px] text-red-700" role="alert">
            {problem}
          </p>
        )}
        <div className="flex gap-2">
          <Button size="sm" data-testid="away-save" onClick={save}>
            {saving ? "Saving..." : "Save away dates"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setStep("dates")}>
            Back
          </Button>
        </div>
      </div>
    )
  }

  const field = (key: keyof AwayDates, label: string, testId: string) => (
    <div className="grid gap-2">
      <Label htmlFor={testId}>{label}</Label>
      <Input
        id={testId}
        data-testid={testId}
        type="date"
        value={dates[key] ?? ""}
        onChange={(e) => setDates((d) => ({ ...d, [key]: e.target.value }))}
      />
    </div>
  )

  return (
    <div className="space-y-4" data-testid="away-dates">
      <div className="flex flex-wrap items-end gap-4">
        {field("start_date", "First day away", "away-start")}
        {field("end_date", "Last day away", "away-end")}
        {field("return_date", "Back on", "away-return")}
      </div>
      {coveringOptions.length > 0 && (
        <div className="grid max-w-sm gap-2">
          <Label htmlFor="away-covering">Covering (optional)</Label>
          <select
            id="away-covering"
            data-testid="away-covering"
            value={dates.covering_user_id ?? ""}
            onChange={(e) => setDates((d) => ({ ...d, covering_user_id: e.target.value }))}
            className="rounded-md border border-neutral-200 px-3 py-2 text-sm"
          >
            <option value="">No one</option>
            {coveringOptions.map((option) => (
              <option key={option.user_id} value={option.user_id}>
                {option.name}
              </option>
            ))}
          </select>
        </div>
      )}
      {problem && (
        <p className="text-[12.5px] text-red-700" role="alert">
          {problem}
        </p>
      )}
      <div className="flex gap-2">
        <Button size="sm" data-testid="away-next" onClick={toReview}>
          {preview.isPending ? "Loading..." : "Next: review the reply"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </div>
  )
}
