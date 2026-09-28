// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What the engine mounts in the portal, in the order a patient meets it:
 * the forms they were asked for first, then messaging, then appointments,
 * then refills.
 *
 * Imported for its side effect by the shell, which is a client component.
 * That is the whole reason this file exists rather than the registrations
 * sitting in the route's page: the page is a server component, so a
 * side-effect import there would fill a registry in the server's module
 * graph and leave the browser's empty — the slots would simply never
 * render.
 *
 * Registration is not a gate. A slot is only ever rendered in the shell's
 * active phase, which needs a live patient session, which needs the portal
 * routes to be mounted — so a deployment that serves no portal renders
 * none of this no matter what is registered here.
 */

"use client"

import { AppointmentsSummary, PortalAppointments } from "@/components/portal/appointments"
import { FormsSummary, PortalForms } from "@/components/portal/forms"
import { MessagingSummary } from "@/components/portal/messaging/MessagingSummary"
import { PortalMessaging } from "@/components/portal/messaging/PortalMessaging"
import { PortalRefills } from "@/components/portal/refills/PortalRefills"
import { RefillsSummary } from "@/components/portal/refills/RefillsSummary"
import { registerPortalSlot, type PortalSlotProps } from "./slots"

function FormsSlot({ slug, sessionToken }: PortalSlotProps) {
  return <PortalForms slug={slug} sessionToken={sessionToken} />
}

function MessagingSlot({ sessionToken }: PortalSlotProps) {
  return <PortalMessaging sessionToken={sessionToken} />
}

function AppointmentsSlot({ sessionToken }: PortalSlotProps) {
  return <PortalAppointments sessionToken={sessionToken} />
}

function RefillsSlot({ sessionToken }: PortalSlotProps) {
  return <PortalRefills sessionToken={sessionToken} />
}

// ``module`` names the capability the deployment has to have turned on for
// this slot to render, and ``label`` is what the navigation calls it. Both
// are presentation: the routes behind an unnamed module are not mounted, so
// what this decides is whether a patient is shown a section their practice
// does not have — not whether they could reach one. ``Summary`` is the
// line on the slot's home-screen tile; each module owns its own, reading
// through the same query key as its section.
//
// The slot is called ``forms`` and its module is ``intake``, which is not a
// slip. The module is the deployment-facing name — it is what
// ``PORTAL_MODULES`` is configured with and what the engine mounts under
// ``/api/patient/intake`` — while the slot id and the label are what the
// patient meets. A person filling in a form has not heard the word intake.
registerPortalSlot({
  id: "forms",
  Component: FormsSlot,
  Summary: FormsSummary,
  module: "intake",
  label: "Forms",
})
registerPortalSlot({
  id: "messaging",
  Component: MessagingSlot,
  Summary: MessagingSummary,
  module: "messaging",
  label: "Messages",
})
// After forms and messaging, which is the product order rather than an accident:
// paperwork is what a practice asks for before a first visit, messaging is
// how a patient reaches them, and appointments is what they come back to
// check. Here the slot id and the module name do coincide — unlike forms
// above — because "appointments" is what both the deployment and the patient
// call it.
registerPortalSlot({
  id: "appointments",
  Component: AppointmentsSlot,
  Summary: AppointmentsSummary,
  module: "appointments",
  label: "Appointments",
})
// Refills comes after appointments: it is an occasional errand between
// visits rather than something a patient meets on the way in. The slot id,
// the module name and what the patient reads all say the same thing here.
registerPortalSlot({
  id: "refills",
  Component: RefillsSlot,
  Summary: RefillsSummary,
  module: "refills",
  label: "Refills",
})
