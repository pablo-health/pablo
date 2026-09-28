// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One portal section on a page of its own — `/portal/{slug}/{section}`,
 * where `section` is a slot id (`forms`, `messaging`, `appointments`,
 * `refills`).
 *
 * The shell is the layout above; which ids exist is the slot registry's
 * business, which lives in the browser — so an unknown id is sent back to
 * Home by the client component, not rejected here.
 *
 * `recover` is not a section: the static `recover/` route beside the
 * `(shell)` group wins over this dynamic segment.
 */

import { PortalSection } from "@/components/portal-shell/PortalSection"

interface PageProps {
  params: Promise<{ section: string }>
}

export default async function PortalSectionRoute({ params }: PageProps) {
  const { section } = await params
  return <PortalSection id={section} />
}
