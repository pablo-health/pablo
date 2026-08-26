// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { useCreateAvailabilityRule, useParseAvailabilityRules } from "@/hooks/useAvailability"
import { ApiError } from "@/lib/api/client"
import type {
  AvailabilityRule,
  EnforcementLevel,
  ParseAvailabilityRulesResponse,
  ProposedAvailabilityRule,
  RuleType,
} from "@/types/availability"
import { RULE_TYPE_LABELS, RuleForm, summarize } from "./AvailabilitySettings"

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return "Something went wrong. Please try again."
}

function proposalToRule(proposal: ProposedAvailabilityRule): AvailabilityRule {
  return {
    id: "",
    user_id: "",
    rule_type: proposal.rule_type,
    enforcement: proposal.enforcement,
    params: proposal.params,
    created_at: null,
    updated_at: null,
  }
}

interface ProposalCardProps {
  proposal: ProposedAvailabilityRule
}

function ProposalCard({ proposal }: ProposalCardProps) {
  const createMutation = useCreateAvailabilityRule()
  const [editing, setEditing] = useState(false)
  const [created, setCreated] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const rule = proposalToRule(proposal)

  function create(ruleType: RuleType, enforcement: EnforcementLevel, params: Record<string, unknown>) {
    setError(null)
    createMutation.mutate(
      { rule_type: ruleType, enforcement, params },
      {
        onSuccess: () => {
          setCreated(true)
          setEditing(false)
        },
        onError: (err) => setError(errorMessage(err)),
      }
    )
  }

  if (editing) {
    return (
      <RuleForm
        initialRule={rule}
        onCancel={() => setEditing(false)}
        onSubmit={create}
        isSaving={createMutation.isPending}
        submitError={error}
      />
    )
  }

  return (
    <li className="rounded-md border border-neutral-200 px-3 py-2">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-neutral-900">
            {RULE_TYPE_LABELS[proposal.rule_type]}
          </p>
          <p className="text-sm text-neutral-600">{summarize(rule)}</p>
          <p className="text-xs text-neutral-500">
            {proposal.enforcement === "hard" ? "Hard — always enforced" : "Soft — allows override"}
          </p>
          {proposal.human_summary && (
            <p className="mt-1 text-xs italic text-neutral-500">{proposal.human_summary}</p>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          {created ? (
            <span className="text-sm text-green-700">Created</span>
          ) : (
            <>
              <Button
                size="sm"
                onClick={() => create(proposal.rule_type, proposal.enforcement, proposal.params)}
                disabled={createMutation.isPending}
              >
                Create
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setEditing(true)}
                disabled={createMutation.isPending}
              >
                Edit
              </Button>
            </>
          )}
        </div>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-red-600">
          {error}
        </p>
      )}
    </li>
  )
}

export function NaturalLanguageRuleEntry() {
  const [text, setText] = useState("")
  const parseMutation = useParseAvailabilityRules()
  const [result, setResult] = useState<ParseAvailabilityRulesResponse | null>(null)
  const [parseError, setParseError] = useState<string | null>(null)

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!text.trim()) return
    setParseError(null)
    setResult(null)
    parseMutation.mutate(
      { text },
      {
        onSuccess: (data) => setResult(data),
        onError: (err) => setParseError(errorMessage(err)),
      }
    )
  }

  return (
    <div className="space-y-3 rounded-md border border-neutral-200 p-4">
      <div>
        <Label htmlFor="nl-availability-text">Describe your availability</Label>
        <p className="text-xs text-neutral-500">
          For example: &ldquo;No appointments on Fridays&rdquo; or &ldquo;9 to 5 on
          weekdays&rdquo;. Each result is a proposal you review before it&apos;s created.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-2">
        <Textarea
          id="nl-availability-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="No appointments on Fridays"
          maxLength={1000}
          disabled={parseMutation.isPending}
        />
        <Button type="submit" size="sm" disabled={parseMutation.isPending || !text.trim()}>
          {parseMutation.isPending ? "Parsing..." : "Parse"}
        </Button>
      </form>

      {parseError && (
        <p role="alert" className="text-sm text-red-600">
          {parseError}
        </p>
      )}

      {result?.could_not_parse && (
        <p className="text-sm text-neutral-600">{result.could_not_parse}</p>
      )}

      {result && result.proposals.length > 0 && (
        <ul className="space-y-2">
          {result.proposals.map((proposal, index) => (
            // Proposals have no stable id until confirmed; index is stable
            // for the lifetime of this parse result.
            <ProposalCard key={index} proposal={proposal} />
          ))}
        </ul>
      )}

      {result?.exclusive && result.existing_conflicting_rules.length > 0 && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          <p className="font-medium">
            You also have working hours that aren&apos;t covered by this description:
          </p>
          <ul className="mt-1 list-disc pl-5">
            {result.existing_conflicting_rules.map((rule) => (
              <li key={rule.id}>{summarize(rule)}</li>
            ))}
          </ul>
          <p className="mt-1 text-xs">
            Remove them in the rules list below if you no longer meet then.
          </p>
        </div>
      )}
    </div>
  )
}
