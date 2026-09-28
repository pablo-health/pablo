// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The portal's home screen — `/portal/{slug}`.
 *
 * The shell around it is the layout beside this file; this page only says
 * that Home is what to draw once there is a session. An invitation link
 * lands here, and so does a patient who has just entered their code.
 */

import { PortalHome } from "@/components/portal-shell/PortalHome"

export default function PortalHomeRoute() {
  return <PortalHome />
}
