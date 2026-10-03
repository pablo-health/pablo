// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { PortalSettings } from "@/lib/api/portalSettings"
import type { PeopleWords } from "@/lib/peopleTerm"

/**
 * When clients cannot reach Messages — the whole portal is off, or the
 * practice turned the Messages part off — what they already sent is still
 * the practice's to read, so it stays in the Inbox. What goes is replying: a
 * reply would land somewhere the client cannot open.
 */
export function portalOffNotice(
  portal: PortalSettings | undefined,
  people: PeopleWords,
): string | null {
  if (portal?.enabled === false) return `Your ${people.one} portal is off.`
  if (portal?.modules?.messaging === false) return `Messages are turned off in your ${people.one} portal.`
  return null
}

export function repliesOffNote(
  portal: PortalSettings | undefined,
  people: PeopleWords,
): string | null {
  if (portal?.enabled === false) return `Turn the ${people.one} portal back on to reply.`
  if (portal?.modules?.messaging === false) return "Turn Messages back on to reply."
  return null
}
