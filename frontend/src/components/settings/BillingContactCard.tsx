// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { SettingsCard } from "@/components/settings/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useUpdateBillingProfile } from "@/hooks/useBillingProfile"
import type { BillingProfileResponse } from "@/types/practiceBilling"
import { Field, type PracticeDetails, type TextField, textPatch } from "./billingProfileShared"
import { useSettingsSaved } from "./SettingsSavedContext"

export const CONTACT_EMAIL_HELP =
  "Use an address you check regularly — this is where enrollment questions arrive."

const OWNED: readonly TextField[] = [
  "address_line1",
  "address_line2",
  "city",
  "state",
  "postal_code",
  "phone",
  "contact_email",
]

/**
 * Where insurers and the clearinghouse reach the practice.
 *
 * Separate from practice identity, with its own Save. The two are different
 * mental tasks — one is paperwork a therapist may have to go and look up, the
 * other she knows off by heart — and asking for both in one form is what made
 * the original card feel like a wall.
 *
 * The email guidance deliberately does not say "use the practice's general
 * address rather than a clinician's own". That is advice for a group practice
 * and nonsense for a solo one, where she IS the practice; it sent a therapist
 * working alone looking for an inbox that does not exist. What matters is that
 * she reads it.
 */
export function BillingContactCard({
  profile,
  practiceDetails,
}: {
  profile: BillingProfileResponse
  practiceDetails?: PracticeDetails
}) {
  const update = useUpdateBillingProfile()
  const { flashSaved } = useSettingsSaved()
  const [draft, setDraft] = useState<Record<TextField, string>>(() => ({
    legal_name: "",
    billing_npi: "",
    address_line1: profile.address_line1 ?? "",
    address_line2: profile.address_line2 ?? "",
    city: profile.city ?? "",
    state: profile.state ?? "",
    postal_code: profile.postal_code ?? "",
    phone: profile.phone ?? "",
    contact_email: profile.contact_email ?? "",
  }))
  const [problem, setProblem] = useState<string | null>(null)

  const patch = textPatch(profile, draft, OWNED)
  const isDirty = Object.keys(patch).length > 0

  const profilePhone = practiceDetails?.phone?.trim() ?? ""
  const profileAddress = practiceDetails?.address?.trim() ?? ""
  const canPrefill =
    Boolean(profilePhone || profileAddress) && !draft.address_line1.trim() && !draft.phone.trim()

  function set(field: TextField, value: string) {
    setDraft((current) => ({ ...current, [field]: value }))
  }

  function handleSave() {
    const invalid =
      draft.state.trim() && draft.state.trim().length !== 2
        ? "State is its two-letter abbreviation."
        : null
    setProblem(invalid)
    if (invalid || !isDirty) return
    update.mutate(patch, {
      onSuccess: () => flashSaved(),
      onError: (error) =>
        setProblem(
          error instanceof Error && error.message
            ? error.message
            : "The billing contact could not be saved.",
        ),
    })
  }

  return (
    <SettingsCard
      title="Billing contact"
      description="Where insurers and your clearinghouse should contact you about enrollments and claims."
    >
      <div className="space-y-4">
        {canPrefill && (
          <p className="text-[12.5px] text-muted-foreground">
            <Button
              type="button"
              variant="link"
              size="sm"
              className="h-auto p-0 text-[12.5px] underline underline-offset-4"
              onClick={() =>
                setDraft((current) => ({
                  ...current,
                  phone: current.phone || profilePhone,
                  address_line1: current.address_line1 || profileAddress,
                }))
              }
            >
              Use my practice details
            </Button>{" "}
            — the address arrives as one line, so split the city, state and ZIP out yourself.
            Nothing is saved until you press Save.
          </p>
        )}

        <div className="grid gap-3">
          <Field id="address-line1" label="Billing address">
            <Input
              id="address-line1"
              value={draft.address_line1}
              onChange={(e) => set("address_line1", e.target.value)}
              placeholder="Street address"
              autoComplete="address-line1"
            />
          </Field>
          <Input
            id="address-line2"
            aria-label="Address line 2 (optional)"
            value={draft.address_line2}
            onChange={(e) => set("address_line2", e.target.value)}
            placeholder="Address line 2 (optional)"
            autoComplete="address-line2"
          />
          <div className="grid gap-3 sm:grid-cols-3">
            <Field id="city" label="City">
              <Input
                id="city"
                value={draft.city}
                onChange={(e) => set("city", e.target.value)}
                autoComplete="address-level2"
              />
            </Field>
            <Field id="state" label="State">
              <Input
                id="state"
                value={draft.state}
                onChange={(e) => set("state", e.target.value)}
                maxLength={2}
                placeholder="GA"
                autoComplete="address-level1"
              />
            </Field>
            <Field id="postal-code" label="ZIP code">
              <Input
                id="postal-code"
                value={draft.postal_code}
                onChange={(e) => set("postal_code", e.target.value)}
                autoComplete="postal-code"
              />
            </Field>
          </div>

          <Field id="phone" label="Phone">
            <Input
              id="phone"
              value={draft.phone}
              onChange={(e) => set("phone", e.target.value)}
              autoComplete="tel"
            />
          </Field>

          <Field id="contact-email" label="Billing email" help={CONTACT_EMAIL_HELP}>
            <Input
              id="contact-email"
              type="email"
              value={draft.contact_email}
              onChange={(e) => set("contact_email", e.target.value)}
              placeholder="billing@yourpractice.com"
              autoComplete="email"
            />
          </Field>
        </div>

        {problem && (
          <p role="alert" className="text-[12.5px] text-destructive">
            {problem}
          </p>
        )}

        {isDirty && (
          <Button size="sm" onClick={handleSave} disabled={update.isPending}>
            {update.isPending ? "Saving..." : "Save"}
          </Button>
        )}
      </div>
    </SettingsCard>
  )
}
