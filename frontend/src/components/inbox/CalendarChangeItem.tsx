// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { formatInUserTimeZone, useUserTimeZone } from "@/hooks/usePreferences"
import { inboxKeys } from "@/hooks/inboxKeys"
import { useAuthMutation } from "@/hooks/useAuthQuery"
import { queryKeys } from "@/lib/api/queryKeys"
import { resolveGoogleChange, resolveHeldGoogleRemovals } from "@/lib/api/scheduling"
import { GOOGLE_SYNC_STATUS, type GoogleChangeResolution } from "@/types/scheduling"
import { NoticeButton } from "@/components/calendar/editorial/GoogleChangeNotice"
import type { InboxItemRendererProps } from "./itemRenderers"
import { ItemPanel, OpenLink, WHEN } from "./ItemPanel"

const INVALIDATE = [inboxKeys.all, queryKeys.appointments.all]

/**
 * A session Google Calendar changed and Pablo did not simply follow. Settled
 * here with the same choices the calendar offers; settling it takes it off
 * the list. A held bulk removal is settled for every held session at once,
 * as it is on the calendar.
 */
export function CalendarChangeItem({ item }: InboxItemRendererProps) {
  const timeZone = useUserTimeZone()
  const appointmentId = item.context.appointment_id
  const status = item.context.google_sync_status
  const startAt = item.context.start_at

  const resolveOne = useAuthMutation<unknown, GoogleChangeResolution>({
    mutationFn: (resolution) => resolveGoogleChange(appointmentId, resolution),
    invalidateKeys: INVALIDATE,
  })
  const resolveHeld = useAuthMutation<unknown, GoogleChangeResolution>({
    mutationFn: (resolution) => resolveHeldGoogleRemovals(resolution),
    invalidateKeys: INVALIDATE,
  })
  const pending = resolveOne.isPending || resolveHeld.isPending

  return (
    <ItemPanel item={item}>
      {startAt && (
        <p className="text-sm text-neutral-800">
          Session on {formatInUserTimeZone(startAt, timeZone, WHEN)}
        </p>
      )}
      <div className="flex flex-wrap gap-2 text-neutral-800" data-testid="inbox-calendar-actions">
        {status === GOOGLE_SYNC_STATUS.externalChange && (
          <>
            <NoticeButton disabled={pending} onClick={() => resolveOne.mutate("keep_pablo")}>
              Keep this time
            </NoticeButton>
            <NoticeButton disabled={pending} onClick={() => resolveOne.mutate("accept_google")}>
              Use Google&apos;s time
            </NoticeButton>
          </>
        )}
        {status === GOOGLE_SYNC_STATUS.removedInGoogle && (
          <>
            <NoticeButton disabled={pending} onClick={() => resolveOne.mutate("keep_pablo")}>
              Undo
            </NoticeButton>
            <NoticeButton disabled={pending} onClick={() => resolveOne.mutate("accept_google")}>
              Keep it cancelled
            </NoticeButton>
          </>
        )}
        {status === GOOGLE_SYNC_STATUS.missingInGoogle && (
          <>
            <NoticeButton disabled={pending} onClick={() => resolveHeld.mutate("keep_pablo")}>
              Put back in Google Calendar
            </NoticeButton>
            <NoticeButton disabled={pending} onClick={() => resolveHeld.mutate("accept_google")}>
              Cancel here too
            </NoticeButton>
          </>
        )}
      </div>
      {(resolveOne.isError || resolveHeld.isError) && (
        <p className="text-sm text-red-700" role="alert">
          That didn&rsquo;t go through. Try again.
        </p>
      )}
      <OpenLink href={item.href} label="Open calendar" />
    </ItemPanel>
  )
}
