// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useQueries, useQueryClient } from "@tanstack/react-query"
import { getSession } from "@/lib/api/sessions"
import { queryKeys } from "@/lib/api/queryKeys"
import { useConfig } from "@/lib/config"
import {
  draftOutcome,
  isDrafting,
  isWatchableDraft,
  markAnnounced,
  toDraftNotice,
  wasAnnounced,
  type DraftNotice,
} from "@/lib/draftNotices"
import { unwatchDraft, useWatchedDrafts, watchDraft } from "@/lib/draftWatch"
import { useSessionList } from "./useSessions"

/** How often the session list is re-read for drafts started elsewhere (a recording). */
const LIST_POLL_MS = 60_000
/** How often a session being drafted is re-read, as the session page does. */
const DRAFT_POLL_MS = 3_000
/** Notices beyond this many are dropped, oldest first. */
const MAX_NOTICES = 3

/**
 * Notices for drafts that landed while the clinician was in the app.
 *
 * Learns session status the way the rest of the app does — the session list,
 * and the session detail polled while a draft is being written — and raises
 * one notice per session when its draft becomes ready or fails. A session is
 * only announced when it was seen being drafted, so opening the app does not
 * announce every draft already waiting; and an announced session is never
 * announced again, across re-renders, reloads or a second tab.
 */
export function useDraftNotices(): {
  notices: DraftNotice[]
  dismiss: (sessionId: string) => void
} {
  const { dataMode } = useConfig()
  const live = dataMode !== "mock"
  const queryClient = useQueryClient()
  const announced = useRef(new Set<string>())
  const [notices, setNotices] = useState<DraftNotice[]>([])

  const { data: list } = useSessionList(undefined, { refetchInterval: LIST_POLL_MS })

  useEffect(() => {
    if (!live || !list) return
    for (const session of list.data) {
      if (isWatchableDraft(session) && !announced.current.has(session.id)) {
        watchDraft(session.id)
      }
    }
  }, [list, live])

  const watched = useWatchedDrafts()
  const drafts = useQueries({
    queries: watched.map((sessionId) => ({
      queryKey: queryKeys.sessions.detail(sessionId),
      queryFn: () => getSession(sessionId),
      enabled: live,
      staleTime: 0,
      refetchInterval: DRAFT_POLL_MS,
    })),
  })

  useEffect(() => {
    const landed: DraftNotice[] = []
    for (const { data: session } of drafts) {
      if (!session || isDrafting(session.status)) continue
      unwatchDraft(session.id)
      const outcome = draftOutcome(session.status)
      if (!outcome || announced.current.has(session.id)) continue
      announced.current.add(session.id)
      if (wasAnnounced(session.id)) continue
      markAnnounced(session.id)
      landed.push(toDraftNotice(session, outcome))
    }
    if (landed.length === 0) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- a notice is raised in response to polled data arriving
    setNotices((current) => [...landed, ...current].slice(0, MAX_NOTICES))
    void queryClient.invalidateQueries({ queryKey: queryKeys.sessions.lists() })
  }, [drafts, queryClient])

  const dismiss = useCallback((sessionId: string) => {
    setNotices((current) => current.filter((notice) => notice.sessionId !== sessionId))
  }, [])

  return { notices, dismiss }
}
