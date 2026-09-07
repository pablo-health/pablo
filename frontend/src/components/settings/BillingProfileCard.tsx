// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * BillingProfileCard
 *
 * The legal entity a claim is filed as: legal name, tax id, billing NPI,
 * address, phone and the inbox the clearinghouse writes to.
 *
 * The tax id is the one field that never round-trips. The entry field is
 * never pre-filled; once a tax id is on file the card shows "ends in 9714"
 * with a Replace action that opens an empty field. Only the last four
 * digits ever reach the browser.
 *
 * Saves are explicit (a Save button once something changed) rather than on
 * blur — a tax id should land as one deliberate act, not on the way past.
 *
 * A card with nothing on it yet offers to start from the practice details the
 * clinician's profile already holds. It fills the draft only — the therapist
 * still saves, because the two records are allowed to differ. The tax id is
 * never copied from anywhere, and the clinician's NPI reaches a claim through
 * their own card rather than this one. The profile keeps the address as one
 * free-text line, so it lands whole on the first address line and the city,
 * state and ZIP are left to be split out by hand.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { SegmentedControl, SettingsCard } from "@/components/settings/ui"
import { useUpdateBillingProfile } from "@/hooks/useBillingProfile"
import type {
  BillingProfileResponse,
  TaxIdType,
  UpdateBillingProfileRequest,
} from "@/types/practiceBilling"
import { useSettingsSaved } from "./SettingsSavedContext"

export const BILLING_NPI_HELP =
  "Only if you bill as a group or organization (a type 2 NPI). Solo practices leave this blank and file under your own NPI, below."

export const CONTACT_EMAIL_HELP =
  "The inbox payers and your clearinghouse write to about enrollments. The practice's general address, not a clinician's own."

type TextField =
  | "legal_name"
  | "billing_npi"
  | "address_line1"
  | "address_line2"
  | "city"
  | "state"
  | "postal_code"
  | "phone"
  | "contact_email"

const TEXT_FIELDS: TextField[] = [
  "legal_name",
  "billing_npi",
  "address_line1",
  "address_line2",
  "city",
  "state",
  "postal_code",
  "phone",
  "contact_email",
]

type Draft = Record<TextField, string> & { tax_id_type: TaxIdType | "" }

function draftFrom(profile: BillingProfileResponse): Draft {
  return {
    legal_name: profile.legal_name ?? "",
    billing_npi: profile.billing_npi ?? "",
    address_line1: profile.address_line1 ?? "",
    address_line2: profile.address_line2 ?? "",
    city: profile.city ?? "",
    state: profile.state ?? "",
    postal_code: profile.postal_code ?? "",
    phone: profile.phone ?? "",
    contact_email: profile.contact_email ?? "",
    tax_id_type: profile.tax_id_type ?? "",
  }
}

/** Only what changed, so an untouched field keeps its stored value. */
function patchFrom(
  profile: BillingProfileResponse,
  draft: Draft,
  taxId: string,
): UpdateBillingProfileRequest {
  const patch: UpdateBillingProfileRequest = {}
  for (const field of TEXT_FIELDS) {
    const next = draft[field].trim()
    if (next !== (profile[field] ?? "")) {
      if (field === "legal_name") {
        if (next) patch.legal_name = next
      } else {
        patch[field] = next || null
      }
    }
  }
  if (draft.tax_id_type && draft.tax_id_type !== profile.tax_id_type) {
    patch.tax_id_type = draft.tax_id_type
  }
  if (taxId.trim()) patch.tax_id = taxId.trim()
  return patch
}

function validate(draft: Draft, taxId: string): string | null {
  if (draft.billing_npi.trim() && !/^\d{10}$/.test(draft.billing_npi.trim())) {
    return "A billing NPI is ten digits."
  }
  if (draft.state.trim() && draft.state.trim().length !== 2) {
    return "State is its two-letter abbreviation."
  }
  const digits = taxId.replace(/\D/g, "")
  if (taxId.trim() && digits.length !== 9) return "A tax id is nine digits."
  if (taxId.trim() && !draft.tax_id_type) return "Say whether the tax id is an EIN or an SSN."
  return null
}

/** What the clinician's profile knows about the practice, as a starting point. */
export interface PracticeDetails {
  name?: string | null
  phone?: string | null
  /** One free-text line, as the professional-info step captured it. */
  address?: string | null
}

interface BillingProfileCardProps {
  profile: BillingProfileResponse
  practiceDetails?: PracticeDetails
}

export function BillingProfileCard({ profile, practiceDetails }: BillingProfileCardProps) {
  const update = useUpdateBillingProfile()
  const { flashSaved } = useSettingsSaved()
  const [draft, setDraft] = useState<Draft>(() => draftFrom(profile))
  const [taxId, setTaxId] = useState("")
  const [replacingTaxId, setReplacingTaxId] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const taxIdOnFile = Boolean(profile.tax_id_last4)
  const showTaxIdEntry = !taxIdOnFile || replacingTaxId
  const patch = patchFrom(profile, draft, taxId)
  const isDirty = Object.keys(patch).length > 0

  const profileName = practiceDetails?.name?.trim() ?? ""
  const profilePhone = practiceDetails?.phone?.trim() ?? ""
  const profileAddress = practiceDetails?.address?.trim() ?? ""
  const canPrefill =
    Boolean(profileName || profilePhone || profileAddress) &&
    !draft.legal_name.trim() &&
    !draft.address_line1.trim()

  function set(field: TextField, value: string) {
    setDraft((current) => ({ ...current, [field]: value }))
  }

  function prefillFromProfile() {
    setDraft((current) => ({
      ...current,
      legal_name: current.legal_name || profileName,
      phone: current.phone || profilePhone,
      address_line1: current.address_line1 || profileAddress,
    }))
  }

  function handleSave() {
    const invalid = validate(draft, taxId)
    setProblem(invalid)
    if (invalid || !isDirty) return
    update.mutate(patch, {
      onSuccess: () => {
        setTaxId("")
        setReplacingTaxId(false)
        flashSaved()
      },
      onError: (error) => {
        setProblem(error instanceof Error && error.message ? error.message : "The profile could not be saved.")
      },
    })
  }

  return (
    <SettingsCard
      title="Practice profile"
      description="The legal entity your claims are filed as. Payers match this against what they have on file for your tax id."
    >
      <div className="space-y-4">
        {canPrefill && (
          <p className="text-[12.5px] text-muted-foreground">
            <Button
              type="button"
              variant="link"
              size="sm"
              className="h-auto p-0 text-[12.5px]"
              onClick={prefillFromProfile}
            >
              Use my profile details
            </Button>{" "}
            — starts from the practice name, phone and address you have already given. The address
            arrives as one line; split the city, state and ZIP out yourself. Nothing is saved
            until you press Save.
          </p>
        )}

        <Field id="legal-name" label="Legal name">
          <Input
            id="legal-name"
            value={draft.legal_name}
            onChange={(e) => set("legal_name", e.target.value)}
            placeholder="As registered with the IRS"
            autoComplete="organization"
          />
        </Field>

        <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
          <Field id="tax-id" label="Tax id">
            {showTaxIdEntry ? (
              <div className="flex gap-2">
                <Input
                  id="tax-id"
                  value={taxId}
                  onChange={(e) => setTaxId(e.target.value)}
                  placeholder={taxIdOnFile ? "New tax id" : "EIN or SSN"}
                  inputMode="numeric"
                  autoComplete="off"
                  data-testid="tax-id-input"
                />
                {taxIdOnFile && (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setTaxId("")
                      setReplacingTaxId(false)
                    }}
                  >
                    Keep current
                  </Button>
                )}
              </div>
            ) : (
              <div className="flex items-center gap-3" data-testid="tax-id-masked">
                <span id="tax-id" className="text-sm text-foreground">
                  Ends in {profile.tax_id_last4}
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setReplacingTaxId(true)}
                >
                  Replace
                </Button>
              </div>
            )}
          </Field>
          <div className="grid gap-1.5">
            <span className="text-sm font-medium">Tax id type</span>
            <SegmentedControl<TaxIdType | "">
              label="Tax id type"
              value={draft.tax_id_type}
              onChange={(next) => setDraft((current) => ({ ...current, tax_id_type: next }))}
              options={[
                { value: "ein", label: "EIN" },
                { value: "ssn", label: "SSN" },
              ]}
            />
          </div>
        </div>
        <p className="text-[12.5px] text-muted-foreground">
          Stored encrypted. Once saved, only the last four digits are ever shown here.
        </p>

        <Field id="billing-npi" label="Billing NPI (optional)" help={BILLING_NPI_HELP}>
          <Input
            id="billing-npi"
            value={draft.billing_npi}
            onChange={(e) => set("billing_npi", e.target.value)}
            inputMode="numeric"
            placeholder="10 digits"
          />
        </Field>

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
            aria-label="Address line 2"
            value={draft.address_line2}
            onChange={(e) => set("address_line2", e.target.value)}
            placeholder="Suite, unit (optional)"
            autoComplete="address-line2"
          />
          <div className="grid gap-3 sm:grid-cols-[2fr_1fr_1fr]">
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
                onChange={(e) => set("state", e.target.value.toUpperCase())}
                maxLength={2}
                placeholder="GA"
                autoComplete="address-level1"
              />
            </Field>
            <Field id="postal-code" label="ZIP">
              <Input
                id="postal-code"
                value={draft.postal_code}
                onChange={(e) => set("postal_code", e.target.value)}
                inputMode="numeric"
                autoComplete="postal-code"
              />
            </Field>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field id="phone" label="Phone">
            <Input
              id="phone"
              value={draft.phone}
              onChange={(e) => set("phone", e.target.value)}
              inputMode="tel"
              placeholder="10 digits"
              autoComplete="tel"
            />
          </Field>
          <Field id="contact-email" label="Contact email" help={CONTACT_EMAIL_HELP}>
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

function Field({
  id,
  label,
  help,
  children,
}: {
  id: string
  label: string
  help?: string
  children: React.ReactNode
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {help && <p className="text-[12.5px] text-muted-foreground">{help}</p>}
    </div>
  )
}
