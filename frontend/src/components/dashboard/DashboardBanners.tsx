// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { useMemo } from "react"
import { useSessionList } from "@/hooks/useSessions"

/**
 * Stack of action banners shown above today's session list — mirrors the
 * Pablo companion's banner stack pattern. Renders nothing when there's
 * nothing urgent, so the dashboard isn't cluttered on a clean day.
 */
export function DashboardBanners() {
  const { data: sessionData } = useSessionList()

  const notesPending = useMemo(
    () =>
      (sessionData?.data ?? []).filter(
        (s) => s.note !== null && s.note.finalized_at === null,
      ).length,
    [sessionData],
  )

  if (notesPending === 0) return null

  return (
    <div className="space-y-2">
      <Banner
        href="/dashboard/sessions"
        message={
          notesPending === 1
            ? "1 note awaiting your signature"
            : `${notesPending} notes awaiting your signature`
        }
      />
    </div>
  )
}

interface BannerProps {
  href: string
  message: string
}

function Banner({ href, message }: BannerProps) {
  return (
    <Link
      href={href}
      className="flex items-center justify-between rounded-lg border border-primary-200 bg-primary-50 px-4 py-3 text-primary-800 hover:bg-primary-100 transition-colors"
    >
      <span className="font-medium">{message}</span>
      <span aria-hidden="true">→</span>
    </Link>
  )
}
