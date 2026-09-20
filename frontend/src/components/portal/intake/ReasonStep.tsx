// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Step two: what brings you in.
 *
 * The prompt is the server's (`reason_prompt`), not this file's. Required,
 * because the route requires it — a submission with an empty reason is
 * rejected, and finding that out after nine PHQ-9 items would be a poor way
 * to learn it.
 */

import { Textarea } from "@/components/ui/textarea"

/** Matches `SubmitIntakeRequest.reason_text`'s server-side cap. */
export const REASON_MAX = 4_000

interface ReasonStepProps {
  prompt: string
  value: string
  onChange: (value: string) => void
}

export function ReasonStep({ prompt, value, onChange }: ReasonStepProps) {
  return (
    <section aria-labelledby="intake-reason-heading">
      <h2 id="intake-reason-heading" className="text-lg font-semibold text-neutral-900">
        {prompt}
      </h2>
      <div className="mt-4 space-y-1.5">
        <Textarea
          id="intake-reason"
          data-testid="intake-reason"
          aria-labelledby="intake-reason-heading"
          className="min-h-[9rem]"
          value={value}
          maxLength={REASON_MAX}
          onChange={(e) => onChange(e.target.value)}
        />
        <p data-testid="intake-reason-counter" className="text-right text-xs text-neutral-500">
          {value.length} / {REASON_MAX}
        </p>
      </div>
    </section>
  )
}
