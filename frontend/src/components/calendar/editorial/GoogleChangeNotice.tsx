// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  GOOGLE_SYNC_STATUS,
  type AppointmentResponse,
  type GoogleChangeResolution,
} from "@/types/scheduling"

export type ResolveGoogleChange = (
  appointment: AppointmentResponse,
  resolution: GoogleChangeResolution,
) => void

/** Whether the sync left this session for the therapist to settle. */
export function needsGoogleDecision(appointment: AppointmentResponse): boolean {
  return (
    appointment.google_sync_status === GOOGLE_SYNC_STATUS.externalChange ||
    appointment.google_sync_status === GOOGLE_SYNC_STATUS.removedInGoogle
  )
}

interface GoogleChangeNoticeProps {
  appointment: AppointmentResponse
  onResolve: ResolveGoogleChange
  pending?: boolean
}

/**
 * What Google Calendar did to one of Pablo's sessions, when Pablo did not
 * simply follow it: a move it couldn't take (it overlapped another session,
 * or became all-day), or a deletion it followed by cancelling quietly.
 * Renders nothing for any other session.
 */
export function GoogleChangeNotice({
  appointment,
  onResolve,
  pending = false,
}: GoogleChangeNoticeProps) {
  const status = appointment.google_sync_status
  if (status === GOOGLE_SYNC_STATUS.externalChange) {
    return (
      <NoticeBox testId="google-change-moved">
        <p>Google Calendar has a different time for this session, and it couldn&apos;t move there.</p>
        <div className="mt-2 flex flex-wrap gap-2">
          <NoticeButton
            disabled={pending}
            onClick={() => onResolve(appointment, "keep_pablo")}
          >
            Keep this time
          </NoticeButton>
          <NoticeButton
            disabled={pending}
            onClick={() => onResolve(appointment, "accept_google")}
          >
            Use Google&apos;s time
          </NoticeButton>
        </div>
      </NoticeBox>
    )
  }
  if (status === GOOGLE_SYNC_STATUS.removedInGoogle) {
    return (
      <NoticeBox testId="google-change-removed">
        <div className="flex flex-wrap items-center gap-2">
          <p className="mr-auto">Removed from Google Calendar, so cancelled here.</p>
          <NoticeButton
            disabled={pending}
            onClick={() => onResolve(appointment, "keep_pablo")}
          >
            Undo
          </NoticeButton>
        </div>
      </NoticeBox>
    )
  }
  return null
}

function NoticeBox({ testId, children }: { testId: string; children: React.ReactNode }) {
  return (
    <div
      data-testid={testId}
      role="status"
      className="mt-4 rounded-xl px-3 py-2.5 text-[13px]"
      style={{
        backgroundColor: "var(--ed-status-noshow-bg)",
        color: "var(--ed-status-noshow-fg)",
      }}
    >
      {children}
    </div>
  )
}

export function NoticeButton({
  onClick,
  disabled,
  children,
}: {
  onClick: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="rounded-full px-3 py-1 text-[12.5px] font-bold disabled:opacity-50"
      style={{ border: "1px solid currentColor" }}
    >
      {children}
    </button>
  )
}
