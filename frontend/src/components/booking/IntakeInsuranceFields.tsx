// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * IntakeInsuranceFields
 *
 * The optional insurance block on the public booking form: what a client
 * reads off their card, before any chart exists. Left off, the booking sends
 * `insurance: null` and nothing is put on file. Filled in, it lands on the
 * new chart's coverage exactly as typed, for the practice to tidy from the
 * payer directory later.
 */

"use client"

import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { SubscriberRelationship } from "@/types/coverage"

export interface IntakeInsuranceForm {
  payer_name: string
  payer_id: string
  member_id: string
  group_number: string
  plan_name: string
  subscriber_relationship: SubscriberRelationship
  subscriber_first_name: string
  subscriber_last_name: string
  subscriber_date_of_birth: string
}

export const EMPTY_INSURANCE: IntakeInsuranceForm = {
  payer_name: "",
  payer_id: "",
  member_id: "",
  group_number: "",
  plan_name: "",
  subscriber_relationship: "self",
  subscriber_first_name: "",
  subscriber_last_name: "",
  subscriber_date_of_birth: "",
}

/** What the booking POST carries, matching `IntakeCoverage` on the server. */
export function intakeInsurancePayload(form: IntakeInsuranceForm) {
  const self = form.subscriber_relationship === "self"
  const orNull = (value: string) => value.trim() || null
  return {
    payer_name: form.payer_name.trim(),
    payer_id: orNull(form.payer_id),
    member_id: form.member_id.trim(),
    group_number: orNull(form.group_number),
    plan_name: orNull(form.plan_name),
    subscriber_relationship: form.subscriber_relationship,
    subscriber_first_name: self ? null : orNull(form.subscriber_first_name),
    subscriber_last_name: self ? null : orNull(form.subscriber_last_name),
    subscriber_date_of_birth: self ? null : orNull(form.subscriber_date_of_birth),
  }
}

interface IntakeInsuranceFieldsProps {
  enabled: boolean
  onEnabledChange: (enabled: boolean) => void
  value: IntakeInsuranceForm
  onChange: (value: IntakeInsuranceForm) => void
}

export function IntakeInsuranceFields({
  enabled,
  onEnabledChange,
  value,
  onChange,
}: IntakeInsuranceFieldsProps) {
  const set = (field: keyof IntakeInsuranceForm) => (next: string) =>
    onChange({ ...value, [field]: next })
  const isSelf = value.subscriber_relationship === "self"

  return (
    <div className="mb-4 rounded-lg border border-border p-3">
      <label className="flex items-center gap-2 text-sm text-neutral-900">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => onEnabledChange(e.target.checked)}
          className="h-4 w-4 rounded border-neutral-300"
        />
        I&apos;d like to use insurance (optional)
      </label>

      {enabled && (
        <div className="mt-4 space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="insurance-payer-name">Insurance company</Label>
              <Input
                id="insurance-payer-name"
                value={value.payer_name}
                onChange={(e) => set("payer_name")(e.target.value)}
                required
                maxLength={255}
              />
            </div>
            <div>
              <Label htmlFor="insurance-payer-id">Payer ID (if on the card)</Label>
              <Input
                id="insurance-payer-id"
                value={value.payer_id}
                onChange={(e) => set("payer_id")(e.target.value)}
                maxLength={80}
              />
            </div>
            <div>
              <Label htmlFor="insurance-member-id">Member ID</Label>
              <Input
                id="insurance-member-id"
                value={value.member_id}
                onChange={(e) => set("member_id")(e.target.value)}
                required
                maxLength={80}
              />
            </div>
            <div>
              <Label htmlFor="insurance-group-number">Group number</Label>
              <Input
                id="insurance-group-number"
                value={value.group_number}
                onChange={(e) => set("group_number")(e.target.value)}
                maxLength={80}
              />
            </div>
          </div>
          <div>
            <Label htmlFor="insurance-plan-name">Plan name</Label>
            <Input
              id="insurance-plan-name"
              value={value.plan_name}
              onChange={(e) => set("plan_name")(e.target.value)}
              maxLength={255}
            />
          </div>
          <div>
            <Label htmlFor="insurance-relationship">Who is the policyholder?</Label>
            <select
              id="insurance-relationship"
              value={value.subscriber_relationship}
              onChange={(e) =>
                onChange({
                  ...value,
                  subscriber_relationship: e.target.value as SubscriberRelationship,
                })
              }
              className="mt-1 h-10 w-full rounded-md border border-border bg-card px-3 text-sm"
            >
              <option value="self">Me</option>
              <option value="spouse">My spouse</option>
              <option value="child">My parent (I&apos;m a dependent)</option>
              <option value="other">Someone else</option>
            </select>
          </div>
          {!isSelf && (
            <div className="grid gap-4 sm:grid-cols-3">
              <div>
                <Label htmlFor="insurance-subscriber-first">Policyholder first name</Label>
                <Input
                  id="insurance-subscriber-first"
                  value={value.subscriber_first_name}
                  onChange={(e) => set("subscriber_first_name")(e.target.value)}
                  maxLength={255}
                />
              </div>
              <div>
                <Label htmlFor="insurance-subscriber-last">Policyholder last name</Label>
                <Input
                  id="insurance-subscriber-last"
                  value={value.subscriber_last_name}
                  onChange={(e) => set("subscriber_last_name")(e.target.value)}
                  maxLength={255}
                />
              </div>
              <div>
                <Label htmlFor="insurance-subscriber-dob">Policyholder date of birth</Label>
                <Input
                  id="insurance-subscriber-dob"
                  type="date"
                  value={value.subscriber_date_of_birth}
                  onChange={(e) => set("subscriber_date_of_birth")(e.target.value)}
                />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
