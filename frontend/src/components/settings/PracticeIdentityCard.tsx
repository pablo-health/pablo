// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { SegmentedControl, SettingsCard } from "@/components/settings/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useUpdateBillingProfile } from "@/hooks/useBillingProfile"
import type {
  BillingProfileResponse,
  TaxIdType,
  UpdateBillingProfileRequest,
} from "@/types/practiceBilling"
import { Field, type PracticeDetails, textPatch } from "./billingProfileShared"
import { useSettingsSaved } from "./SettingsSavedContext"

export const BILLING_NPI_HELP =
  "Enter your Type 2 NPI if you bill as a group or organization. Solo practitioners can leave this blank."

const OWNED = ["legal_name", "billing_npi"] as const

/**
 * How insurers identify the practice: legal name, tax ID, organisation NPI.
 *
 * Its own card with its own Save, separate from the billing contact details.
 * Filling in a practice's paperwork is a lot to ask in one sitting, and these
 * are the parts a therapist is most likely to need to go and look up — so they
 * save on their own and she can come back for the rest.
 *
 * The tax ID is the one field that never round-trips. The entry box is never
 * pre-filled; once an ID is on file the card shows "ends in 9714" with a
 * Change action that opens an empty field. Only the last four digits ever
 * reach the browser, and saving is an explicit act rather than something that
 * happens on the way past.
 */
export function PracticeIdentityCard({
  profile,
  practiceDetails,
}: {
  profile: BillingProfileResponse
  practiceDetails?: PracticeDetails
}) {
  const update = useUpdateBillingProfile()
  const { flashSaved } = useSettingsSaved()
  const [legalName, setLegalName] = useState(profile.legal_name ?? "")
  const [billingNpi, setBillingNpi] = useState(profile.billing_npi ?? "")
  const [taxIdType, setTaxIdType] = useState<TaxIdType | "">(profile.tax_id_type ?? "")
  const [taxId, setTaxId] = useState("")
  const [changingTaxId, setChangingTaxId] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const taxIdOnFile = Boolean(profile.tax_id_last4)
  const showTaxIdEntry = !taxIdOnFile || changingTaxId

  const patch: UpdateBillingProfileRequest = {
    ...textPatch(profile, { legal_name: legalName, billing_npi: billingNpi }, OWNED),
    ...(taxIdType && taxIdType !== profile.tax_id_type ? { tax_id_type: taxIdType } : {}),
    ...(taxId.trim() ? { tax_id: taxId.trim() } : {}),
  }
  const isDirty = Object.keys(patch).length > 0

  // Shown when she already has one, so a value on file never disappears, and
  // otherwise only when it could apply — an organisation NPI is meaningless to
  // a sole proprietor filing under an SSN, and an empty box she has to reason
  // about is worse than no box.
  const showBillingNpi = Boolean(billingNpi.trim()) || taxIdType !== "ssn"

  const profileName = practiceDetails?.name?.trim() ?? ""
  const canPrefill = Boolean(profileName) && !legalName.trim()

  function handleSave() {
    const invalid = validate()
    setProblem(invalid)
    if (invalid || !isDirty) return
    update.mutate(patch, {
      onSuccess: () => {
        setTaxId("")
        setChangingTaxId(false)
        flashSaved()
      },
      onError: (error) =>
        setProblem(
          error instanceof Error && error.message
            ? error.message
            : "The practice identity could not be saved.",
        ),
    })
  }

  function validate(): string | null {
    if (billingNpi.trim() && !/^\d{10}$/.test(billingNpi.trim())) {
      return "An organization NPI is ten digits."
    }
    const digits = taxId.replace(/\D/g, "")
    if (taxId.trim() && digits.length !== 9) return "A tax ID is nine digits."
    if (taxId.trim() && !taxIdType) return "Say whether the tax ID is an EIN or an SSN."
    return null
  }

  return (
    <SettingsCard
      title="Practice identity"
      description="The details insurers use to identify your practice. They match these against what they hold for your tax ID."
    >
      <div className="space-y-4">
        {canPrefill && (
          <p className="text-[12.5px] text-muted-foreground">
            <Button
              type="button"
              variant="link"
              size="sm"
              className="h-auto p-0 text-[12.5px] underline underline-offset-4"
              onClick={() => setLegalName(profileName)}
            >
              Use my practice name
            </Button>{" "}
            — from the details you have already given. Nothing is saved until you press Save.
          </p>
        )}

        <Field id="legal-name" label="Legal business name">
          <Input
            id="legal-name"
            value={legalName}
            onChange={(e) => setLegalName(e.target.value)}
            placeholder="As registered with the IRS"
            autoComplete="organization"
          />
        </Field>

        <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
          <Field id="tax-id" label="Tax ID">
            {showTaxIdEntry ? (
              <Input
                id="tax-id"
                value={taxId}
                onChange={(e) => setTaxId(e.target.value)}
                placeholder={taxIdOnFile ? "New tax ID" : "EIN or SSN"}
                inputMode="numeric"
                autoComplete="off"
                data-testid="tax-id-input"
              />
            ) : (
              <div className="flex items-center gap-2 text-sm">
                <span className="text-neutral-700">Ends in {profile.tax_id_last4}</span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setChangingTaxId(true)}
                >
                  Change
                </Button>
              </div>
            )}
          </Field>
          <Field id="tax-id-type" label="Tax ID type">
            <SegmentedControl
              label="Tax ID type"
              value={taxIdType}
              onChange={(value) => setTaxIdType(value as TaxIdType)}
              options={[
                { value: "ein", label: "EIN" },
                { value: "ssn", label: "SSN" },
              ]}
            />
          </Field>
        </div>
        <p className="text-[12.5px] text-muted-foreground">
          Your tax ID is encrypted. After you save it, only the last four digits will be shown.
        </p>

        {showBillingNpi && (
          <Field id="billing-npi" label="Organization NPI (optional)" help={BILLING_NPI_HELP}>
            <Input
              id="billing-npi"
              value={billingNpi}
              onChange={(e) => setBillingNpi(e.target.value)}
              inputMode="numeric"
              placeholder="10 digits"
            />
          </Field>
        )}

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
