// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { usePortalSettings } from "@/hooks/usePortalSettings"
import { ClientEmailSenderCard } from "../intake/ClientEmailSenderCard"
import { IntakeDocumentsCard } from "../intake/IntakeDocumentsCard"
import { IntakeFormsCard } from "../intake/IntakeFormsCard"
import { InviteEmailCard } from "../intake/InviteEmailCard"
import { LicensedInstrumentsCard } from "../intake/LicensedInstrumentsCard"
import { PortalOfferingCard } from "../intake/PortalOfferingCard"
import { PortalWelcomeCard } from "../intake/PortalWelcomeCard"
import { SettingsCard } from "../ui"

/**
 * Practice > Patient portal.
 *
 * Gated behind `patient_portal`, so this only renders where a deployment has
 * turned the portal on. The first card is whether this practice offers it;
 * the welcome and the invitation only matter once it does, so they wait,
 * greyed, until then — and so does who client email is from. Forms and the documents they can ask somebody to sign
 * stay open either way — a practice can build them before it offers the
 * portal.
 *
 * Documents come after forms because that is the order a practice meets
 * them: the form is the thing being built, and a document is something a
 * question on it points at. Licensed instruments come last for the same
 * reason: a practice arrives at it from a measure the form builder would
 * not let it add.
 */
export function PatientPortalPage() {
  const { data: settings } = usePortalSettings()
  const people = usePeopleTerm()
  // Until the answer arrives, nothing is greyed: a slow read must not flash
  // a practice's own settings as unavailable.
  const off = settings?.enabled === false

  return (
    <>
      <PortalOfferingCard />
      <IntakeFormsCard />
      {off && (
        <p className="mb-3 text-sm text-muted-foreground" data-testid="portal-off-note">
          Turn on the {people.one} portal to invite {people.many}.
        </p>
      )}
      <div
        inert={off}
        aria-disabled={off || undefined}
        className={off ? "opacity-50" : undefined}
        data-testid="portal-client-facing-settings"
      >
        <PortalWelcomeCard />
        <InviteEmailCard />
        <ClientEmailSenderCard />
      </div>
      <IntakeDocumentsCard />
      <LicensedInstrumentsCard />
      <SettingsCard title={`${people.One} sign-in`}>
        <p className="text-sm text-muted-foreground">
          How people get into the portal will be configured here.
        </p>
      </SettingsCard>
    </>
  )
}
