// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What the engine mounts in the portal, in the order a patient meets it:
 * the forms they were asked for first, then messaging.
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

import { PortalForms } from "@/components/portal/forms"
import { PortalMessaging } from "@/components/portal/messaging/PortalMessaging"
import { registerPortalSlot, type PortalSlotProps } from "./slots"

function FormsSlot({ sessionToken }: PortalSlotProps) {
  return <PortalForms sessionToken={sessionToken} />
}

function MessagingSlot({ sessionToken }: PortalSlotProps) {
  return <PortalMessaging sessionToken={sessionToken} />
}

registerPortalSlot({ id: "forms", Component: FormsSlot })
registerPortalSlot({ id: "messaging", Component: MessagingSlot })
