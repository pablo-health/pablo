// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ClaimsSetupChecklist
 *
 * What claims still need before the first one can go out, above the Claims
 * tab: the practice profile filled in, a payer on the list, a client with a
 * plan on file. Each row links to the page that does it.
 *
 * Guidance, not a gate. Claims setup is progressive — a practice can read the
 * tracker, file for the one client who is ready, and come back for the rest —
 * so this sits above the tab's content and never stands in for it. A hard
 * prerequisite belongs in `BillingSetupGate`, which wraps the tabs instead.
 *
 * The profile step reads the same gaps a claim review refuses on
 * (`billingProfileGaps`), so it names what the settings page names.
 */

"use client"

import Link from "next/link"
import { CheckCircle2, Circle } from "lucide-react"
import { billingProfileGaps } from "@/components/settings/billingProfileGaps"
import {
  BILLING_PROFILE_SETTINGS_PATH,
  INSURANCE_PAYERS_SETTINGS_PATH,
} from "@/components/settings/paths"
import { useSettingsUserStatus } from "@/components/settings/useSettingsPreferences"
import { useUnbilledQueue } from "@/hooks/useBilling"
import { useBillingProfile } from "@/hooks/useBillingProfile"
import { useClaims } from "@/hooks/useClaims"
import { usePayers } from "@/hooks/useCoverage"
import { ClaimsSetupSteps } from "./billingSlots.extensions"

interface SetupStep {
  id: string
  label: string
  href: string
  detail: string
  done: boolean
}

export function ClaimsSetupChecklist() {
  const { data: profile } = useBillingProfile()
  const { data: user } = useSettingsUserStatus()
  const { data: payers } = usePayers()
  const { data: queue } = useUnbilledQueue()
  const { data: claims } = useClaims()

  // Nothing to say until every read is in: a checklist that flashes on for a
  // practice that finished setting up months ago is worse than a late one.
  if (!profile || !user || !payers || !queue || !claims) return null

  const gaps = billingProfileGaps(profile, {
    npi_number: user.npi_number,
    taxonomy_code: user.taxonomy_code,
  })

  // There is no practice-wide coverage list to count: coverage lives on the
  // chart, one client at a time. Two signals stand in for it — an unbilled
  // session whose client has a plan on file, or a claim already filed, which
  // could not exist without one.
  const uncovered = queue.items.find((item) => !item.has_coverage)
  const covered = queue.items.some((item) => item.has_coverage) || claims.total > 0

  const steps: SetupStep[] = [
    {
      id: "profile",
      label: "Fill in your practice profile",
      href: BILLING_PROFILE_SETTINGS_PATH,
      detail:
        gaps.claims.length > 0
          ? `Claims still need ${gaps.claims.join(", ")}.`
          : "Who your claims are filed by.",
      done: gaps.claims.length === 0,
    },
    {
      id: "payers",
      label: "Add the payers you file with",
      href: INSURANCE_PAYERS_SETTINGS_PATH,
      detail: "Who you bill, and the filing deadlines each one holds you to.",
      done: payers.total > 0,
    },
    {
      id: "coverage",
      label: "Put a client's plan on file",
      href: uncovered ? `/dashboard/patients/${uncovered.patient_id}` : "/dashboard/patients",
      detail: "A claim is built from the coverage on the client's Insurance tab.",
      done: covered,
    },
  ]

  if (steps.every((step) => step.done)) return null

  return (
    <div className="card space-y-4" data-testid="claims-setup-checklist">
      <div>
        <h2 className="text-lg font-display font-semibold text-neutral-900">
          Before your first claim
        </h2>
        <p className="mt-1 text-sm text-neutral-600">
          What claims still need from you. This goes away once each one is done; the
          tracker below works in the meantime.
        </p>
      </div>
      <ul className="space-y-3">
        {steps.map((step) => (
          <li
            key={step.id}
            className="flex items-start gap-2"
            data-testid={`claims-setup-step-${step.id}`}
          >
            {step.done ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" aria-hidden />
            ) : (
              <Circle className="mt-0.5 h-4 w-4 shrink-0 text-neutral-300" aria-hidden />
            )}
            <div className="text-sm">
              <span className="sr-only">{step.done ? "Done: " : "Not done: "}</span>
              <Link href={step.href} className="font-medium text-neutral-900 hover:underline">
                {step.label}
              </Link>
              <p className="text-[12.5px] text-neutral-600">{step.detail}</p>
            </div>
          </li>
        ))}
        <ClaimsSetupSteps />
      </ul>
    </div>
  )
}
