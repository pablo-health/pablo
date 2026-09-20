// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { SettingsBadge, SettingsCard } from "@/components/settings/ui"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  useAttestInstrument,
  useInstruments,
  useRevokeInstrumentAttestation,
} from "@/hooks/useInstruments"
import type { Instrument } from "@/types/instruments"
import {
  LICENSED_CHECKBOX,
  LICENSED_DESCRIPTION,
  LICENSED_EMPTY,
  LICENSED_ON_FILE,
  LICENSED_SAVE,
  LICENSED_TITLE,
  LICENSED_WITHDRAW,
  LICENSE_REFERENCE_LABEL,
  LICENSE_REFERENCE_PLACEHOLDER,
  SOLD_GUIDANCE,
  SOLD_TITLE,
} from "./intakeCopy"

/** What the server said, or a plain fallback if it said nothing readable. */
function messageOf(error: unknown): string | null {
  if (!error) return null
  if (error instanceof Error && error.message) return error.message
  return "That could not be saved."
}

interface RestrictedRowProps {
  instrument: Instrument
  onAttest: (reference: string) => void
  onWithdraw: () => void
  busy?: boolean
}

/**
 * One measure whose use is restricted, and what this practice has recorded.
 *
 * The box is ticked when permission is on file and untickable back off: the
 * way to take it back is the button beside it, which is an act the server
 * records rather than a control that toggles. Two ways to undo one thing is
 * how a practice ends up unsure which one it used.
 */
function RestrictedRow({ instrument, onAttest, onWithdraw, busy }: RestrictedRowProps) {
  const [checked, setChecked] = useState(false)
  const [reference, setReference] = useState("")
  const fieldId = `license-${instrument.code}`

  return (
    <li className="rounded-xl border border-border p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <span className="text-sm font-semibold text-foreground">
            {instrument.display_name}
          </span>
          <span className="ml-2 text-[12.5px] text-muted-foreground">
            {`${instrument.item_count} items`}
          </span>
        </div>
        {instrument.attested && <SettingsBadge>{LICENSED_ON_FILE}</SettingsBadge>}
      </div>

      <p className="mt-1 text-[13px] text-muted-foreground">
        {instrument.rights_note}
        {instrument.publisher_url && (
          <>
            {" "}
            <a
              className="underline"
              href={instrument.publisher_url}
              target="_blank"
              rel="noreferrer"
            >
              {instrument.publisher_url.replace(/^https:\/\//, "")}
            </a>
          </>
        )}
      </p>

      {instrument.attested ? (
        <div className="mt-3 flex items-center gap-3">
          {instrument.license_reference && (
            <span className="text-[12.5px] text-muted-foreground">
              {instrument.license_reference}
            </span>
          )}
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onWithdraw}
            disabled={busy}
          >
            {LICENSED_WITHDRAW}
          </Button>
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          <div className="flex items-start gap-2">
            <Checkbox
              id={`${fieldId}-holds`}
              checked={checked}
              onCheckedChange={(next) => setChecked(next === true)}
            />
            <Label htmlFor={`${fieldId}-holds`} className="text-[13px] font-normal leading-snug">
              {LICENSED_CHECKBOX}
            </Label>
          </div>
          <div>
            <Label htmlFor={`${fieldId}-reference`}>{LICENSE_REFERENCE_LABEL}</Label>
            <Input
              id={`${fieldId}-reference`}
              value={reference}
              placeholder={LICENSE_REFERENCE_PLACEHOLDER}
              onChange={(e) => setReference(e.target.value)}
            />
          </div>
          <Button
            type="button"
            size="sm"
            onClick={() => onAttest(reference)}
            disabled={!checked || busy}
          >
            {LICENSED_SAVE}
          </Button>
        </div>
      )}
    </li>
  )
}

/**
 * Practice > Patient portal > Licensed instruments.
 *
 * Two lists, and the difference between them is what a practice can do about
 * it. The first is measures whose wording Pablo carries and whose USE the
 * publisher restricts: tick the box, and the form builder offers them. The
 * second is measures sold as a product — Pablo will not carry those
 * questions, so the row says what to do instead rather than offering a
 * control that would do nothing.
 *
 * Measures that need no permission are not on this screen at all. A list of
 * things that are already fine is a list nobody reads.
 */
export function LicensedInstrumentsCard() {
  const { data: instruments } = useInstruments()
  const attest = useAttestInstrument()
  const withdraw = useRevokeInstrumentAttestation()

  const list = instruments ?? []
  const restricted = list.filter((i) => i.rights === "attestation_required")
  const sold = list.filter((i) => i.rights === "never_ship")
  const error = messageOf(attest.error) ?? messageOf(withdraw.error)
  const busy = attest.isPending || withdraw.isPending

  return (
    <SettingsCard title={LICENSED_TITLE} description={LICENSED_DESCRIPTION}>
      {restricted.length === 0 && sold.length === 0 && (
        <p className="text-[13px] text-muted-foreground">{LICENSED_EMPTY}</p>
      )}

      <ul className="space-y-2">
        {restricted.map((instrument) => (
          <RestrictedRow
            key={instrument.code}
            instrument={instrument}
            busy={busy}
            onAttest={(reference) =>
              attest.mutate({
                code: instrument.code,
                input: { license_reference: reference || null },
              })
            }
            onWithdraw={() => withdraw.mutate(instrument.code)}
          />
        ))}
      </ul>

      {error && (
        <p role="alert" className="mt-3 text-[13px] text-destructive">
          {error}
        </p>
      )}

      {sold.length > 0 && (
        <div className="mt-4 border-t border-border pt-3">
          <h4 className="text-sm font-semibold text-foreground">{SOLD_TITLE}</h4>
          <p className="mt-1 text-[13px] text-muted-foreground">{SOLD_GUIDANCE}</p>
          <ul className="mt-2 space-y-1">
            {sold.map((instrument) => (
              <li key={instrument.code} className="text-[13px] text-muted-foreground">
                <span className="font-medium text-foreground">{instrument.display_name}</span>
                {" — "}
                {instrument.publisher_url ? (
                  <a
                    className="underline"
                    href={instrument.publisher_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {instrument.rights_note}
                  </a>
                ) : (
                  instrument.rights_note
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </SettingsCard>
  )
}
