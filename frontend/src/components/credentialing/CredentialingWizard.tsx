// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Check, CircleDashed } from "lucide-react"
import { useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useConfirmations,
  useChecklist,
  useRecordConfirmation,
  useSaveChecklistAnswers,
} from "@/hooks/useCredentialingChecklist"
import type { Confirmation, ChecklistTier, TierProgress } from "@/types/credentialing"
import { ConfirmCard } from "./ConfirmCard"
import { TIERS, fieldsForTier, groupBySection, sectionLabel } from "./tiers"

/**
 * The tiered credentialing checklist.
 *
 * The question set comes from the server and is not restated here. That is the
 * point: the supervision fork changes what is asked, the API enforces it, and a
 * second copy of the branching rule in the client would eventually disagree
 * with the one that decides what gets stored.
 *
 * Two things this screen must never do, both design decisions rather than
 * styling ones. It must not show a single overall progress figure — finishing
 * the practice tier and stopping is a complete outcome, and one number renders
 * that as half done. And it must not present the practice tier as billing
 * setup: every field in it is on a payer application too, which is why the
 * tier's own blurb says so.
 */
export function CredentialingWizard() {
  // Held locally so answering the supervision fork re-narrows the page she is
  // standing on, before anything is saved.
  const [branch, setBranch] = useState<{ supervised?: boolean }>({})
  const [tier, setTier] = useState<ChecklistTier>("tier_0_confirm")

  const { data: checklist, isLoading } = useChecklist(branch)
  const { data: confirmations = [] } = useConfirmations()
  const recordConfirmation = useRecordConfirmation()
  const saveAnswers = useSaveChecklistAnswers()

  const confirmationByKey = useMemo(() => {
    const m = new Map<string, Confirmation>()
    for (const c of confirmations) m.set(c.field_key, c)
    return m
  }, [confirmations])

  const progressByTier = useMemo(() => {
    const m = new Map<ChecklistTier, TierProgress>()
    for (const p of checklist?.progress ?? []) m.set(p.tier, p)
    return m
  }, [checklist])

  if (isLoading || !checklist) {
    return (
      <div className="space-y-3" data-testid="checklist-loading">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  }

  const current = TIERS.find((t) => t.id === tier) ?? TIERS[0]
  const fields = fieldsForTier(checklist.fields, tier)

  return (
    <div className="space-y-6">
      <TierSteps
        selected={tier}
        onSelect={setTier}
        progressByTier={progressByTier}
        claimsReady={checklist.claims_ready}
      />

      <header>
        <h2 className="text-lg font-medium text-stone-900">{current.label}</h2>
        <p className="mt-1 max-w-2xl text-sm text-stone-600">{current.blurb}</p>
      </header>

      {tier === "tier_1_claims_ready" && (
        <SupervisionFork
          supervised={branch.supervised ?? checklist.supervised}
          onChange={(supervised) => {
            setBranch({ supervised })
            saveAnswers.mutate({
              supervision_status: supervised ? "supervised" : "independent",
            })
          }}
        />
      )}

      <div className="space-y-6">
        {groupBySection(fields).map(([section, sectionFields]) => (
          <section key={section} className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-stone-500">
              {sectionLabel(section)}
            </h3>
            <div className="space-y-2">
              {sectionFields.map((field) =>
                tier === "tier_0_confirm" ? (
                  <ConfirmCard
                    key={field.key}
                    field={field}
                    confirmation={confirmationByKey.get(field.key)}
                    // The record first, her last-confirmed value second. That
                    // order is what makes a column that has since changed
                    // visible: she is re-asked about the new value rather than
                    // shown the one she already agreed with.
                    presentedValue={
                      field.current_value ??
                      confirmationByKey.get(field.key)?.presented_value ??
                      null
                    }
                    pending={recordConfirmation.isPending}
                    onRecord={(fieldKey, payload) =>
                      recordConfirmation.mutate({ fieldKey, payload })
                    }
                  />
                ) : (
                  <QuestionRow key={field.key} field={field} />
                ),
              )}
            </div>
          </section>
        ))}
      </div>

      {tier === "tier_1_claims_ready" && checklist.claims_ready && <FinishedForNow />}
    </div>
  )
}

interface TierStepsProps {
  selected: ChecklistTier
  onSelect: (tier: ChecklistTier) => void
  progressByTier: Map<ChecklistTier, TierProgress>
  claimsReady: boolean
}

/**
 * One control per tier, each carrying its own count.
 *
 * Deliberately not a single bar. Two numbers that stand apart are what let
 * "practice details: done" coexist with "panels: not started" without the
 * second reading as a failure.
 */
function TierSteps({ selected, onSelect, progressByTier }: TierStepsProps) {
  return (
    <div className="flex flex-wrap gap-2" role="tablist" aria-label="Checklist steps">
      {TIERS.map((t) => {
        const progress = progressByTier.get(t.id)
        const done = progress?.complete ?? false
        const active = selected === t.id
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onSelect(t.id)}
            className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors ${
              active
                ? "border-stone-900 bg-stone-900 text-white"
                : "border-stone-200 bg-white text-stone-700 hover:bg-stone-50"
            }`}
          >
            {done ? (
              <Check className="h-4 w-4 text-emerald-500" aria-hidden />
            ) : (
              <CircleDashed className="h-4 w-4 opacity-60" aria-hidden />
            )}
            <span>{t.label}</span>
            {progress && progress.required > 0 && (
              <span className={active ? "text-stone-300" : "text-stone-400"}>
                {progress.answered}/{progress.required}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

function SupervisionFork({
  supervised,
  onChange,
}: {
  supervised: boolean
  onChange: (supervised: boolean) => void
}) {
  return (
    <div className="rounded-xl border border-stone-200 bg-stone-50 p-4">
      <p className="text-sm font-medium text-stone-900">
        Are you independently licensed, or practising under supervision?
      </p>
      <p className="mt-1 text-xs text-stone-600">
        Asked first because it changes what follows. Payers treat an
        associate-licensed clinician as a different applicant, not the same one
        with extra fields.
      </p>
      <div className="mt-3 flex gap-2">
        <Button
          type="button"
          size="sm"
          variant={supervised ? "outline" : "default"}
          onClick={() => onChange(false)}
        >
          Independently licensed
        </Button>
        <Button
          type="button"
          size="sm"
          variant={supervised ? "default" : "outline"}
          onClick={() => onChange(true)}
        >
          Under supervision
        </Button>
      </div>
    </div>
  )
}

/**
 * A question that is not a confirmation.
 *
 * The editors for each kind live with the record surfaces that already own
 * them — licences, locations, policies — so this row states what is wanted and
 * whether it is on file rather than duplicating those forms here.
 */
function QuestionRow({ field }: { field: { key: string; label: string; help_text: string | null; required: boolean; answered: boolean } }) {
  return (
    <div className="flex items-start justify-between gap-4 rounded-xl border border-stone-200 bg-white p-4">
      <div className="min-w-0">
        <p className="text-sm font-medium text-stone-900">
          {field.label}
          {!field.required && (
            <span className="ml-2 text-xs font-normal text-stone-500">Optional</span>
          )}
        </p>
        {field.help_text && (
          <p className="mt-1 max-w-xl text-xs text-stone-600">{field.help_text}</p>
        )}
      </div>
      <span
        className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${
          field.answered
            ? "bg-emerald-50 text-emerald-700"
            : "bg-stone-100 text-stone-600"
        }`}
      >
        {field.answered ? "On file" : "Not yet"}
      </span>
    </div>
  )
}

/**
 * What she sees when the practice tier is complete.
 *
 * A finish, not a landing. No nag toward the panels tier, no bar left sitting
 * at two thirds — she has given us everything we need, and the next tier is an
 * offer she can decline.
 */
function FinishedForNow() {
  return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4">
      <p className="text-sm font-medium text-emerald-900">
        That is everything we need. You are done.
      </p>
      <p className="mt-1 text-sm text-emerald-800">
        Your practice details are complete. That is everything a payer
        application asks for short of the credentialing questions, and
        everything we need to bill correctly if you use us for that. Whichever
        you came for, you are not owed anything else today.
      </p>
    </div>
  )
}
