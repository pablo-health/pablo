// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * DraftNotices — one notice per session whose draft lands while the
 * clinician is in the app, and never a second one for the same session.
 *
 * Session status is mocked at the API: the list says which sessions are being
 * drafted, the detail says where each one is now. Refetching by hand stands in
 * for the polling interval.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { act, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { QueryClient } from "@tanstack/react-query"
import { DraftNotices } from "../DraftNotices"
import * as sessionsApi from "@/lib/api/sessions"
import { resetWatchedDrafts, watchDraft } from "@/lib/draftWatch"
import { draftSessionLabel, isWatchableDraft } from "@/lib/draftNotices"
import { renderWithProviders } from "@/test/renderWithProviders"
import { createMockSession } from "@/test/factories"
import type { SessionResponse, SessionStatus } from "@/types/sessions"

vi.mock("@/lib/api/sessions")
vi.mock("@/lib/config", () => ({
  useConfig: () => ({ dataMode: "api" }),
}))

const SESSION_DATE = "2026-10-06T14:00:00"
const LABEL = draftSessionLabel(SESSION_DATE)

function session(status: SessionStatus, id = "session-1"): SessionResponse {
  return createMockSession({
    id,
    status,
    session_date: SESSION_DATE,
    created_at: new Date().toISOString(),
  })
}

/** What the list shows, and what each session's detail shows now. */
function serve(list: SessionResponse[], detail: Record<string, SessionStatus>) {
  vi.mocked(sessionsApi.listSessions).mockResolvedValue({
    data: list,
    total: list.length,
    page: 1,
    page_size: 20,
  })
  vi.mocked(sessionsApi.getSession).mockImplementation(async (id) =>
    session(detail[id], id),
  )
}

async function poll(queryClient: QueryClient) {
  await act(async () => {
    await queryClient.refetchQueries({ queryKey: ["sessions"] })
  })
}

async function seenDrafting(id = "session-1"): Promise<QueryClient> {
  serve([session("processing", id)], { [id]: "processing" })
  const { queryClient } = renderWithProviders(<DraftNotices />)
  await waitFor(() => expect(sessionsApi.getSession).toHaveBeenCalledWith(id))
  return queryClient
}

describe("DraftNotices", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetWatchedDrafts()
    window.localStorage.clear()
  })

  it("announces a draft that becomes ready, linking to its session", async () => {
    const queryClient = await seenDrafting()
    expect(screen.queryByTestId("draft-notice")).not.toBeInTheDocument()

    serve([session("pending_review")], { "session-1": "pending_review" })
    await poll(queryClient)

    const notice = await screen.findByTestId("draft-notice")
    expect(notice).toHaveTextContent(`Draft ready: ${LABEL} session`)
    expect(screen.getByRole("link", { name: "Review draft" })).toHaveAttribute(
      "href",
      "/dashboard/sessions/session-1",
    )
  })

  it("announces a draft that failed, linking to its session", async () => {
    const queryClient = await seenDrafting()

    serve([session("failed")], { "session-1": "failed" })
    await poll(queryClient)

    const notice = await screen.findByTestId("draft-notice")
    expect(notice).toHaveTextContent(`Couldn't draft the ${LABEL} session`)
    expect(screen.getByRole("link", { name: "Open session" })).toHaveAttribute(
      "href",
      "/dashboard/sessions/session-1",
    )
  })

  it("names the session by weekday and time, never by client", async () => {
    const queryClient = await seenDrafting()
    serve([session("pending_review")], { "session-1": "pending_review" })
    await poll(queryClient)

    const notice = await screen.findByTestId("draft-notice")
    expect(notice).toHaveTextContent(/^Draft ready: \w+day \d{1,2}:\d{2} [AP]M session/)
    expect(notice).not.toHaveTextContent("Doe")
  })

  it("announces each session once however often it is read again", async () => {
    const queryClient = await seenDrafting()
    serve([session("pending_review")], { "session-1": "pending_review" })
    await poll(queryClient)
    await screen.findByTestId("draft-notice")

    await poll(queryClient)
    await poll(queryClient)

    expect(screen.getAllByTestId("draft-notice")).toHaveLength(1)
  })

  it("does not announce again after a reload or in a second tab", async () => {
    const first = await seenDrafting()
    serve([session("pending_review")], { "session-1": "pending_review" })
    await poll(first)
    await screen.findByTestId("draft-notice")

    // A second page that also saw the session mid-draft (another tab, or
    // the same tab reloaded before its list caught up).
    resetWatchedDrafts()
    const second = await seenDrafting()
    serve([session("pending_review")], { "session-1": "pending_review" })
    await poll(second)

    await waitFor(() => expect(sessionsApi.getSession).toHaveBeenLastCalledWith("session-1"))
    expect(screen.getAllByTestId("draft-notice")).toHaveLength(1)
  })

  it("does not announce drafts that were already waiting when the app opened", async () => {
    serve([session("pending_review"), session("failed", "session-2")], {
      "session-1": "pending_review",
      "session-2": "failed",
    })
    const { queryClient } = renderWithProviders(<DraftNotices />)
    await waitFor(() => expect(sessionsApi.listSessions).toHaveBeenCalled())
    await poll(queryClient)

    expect(sessionsApi.getSession).not.toHaveBeenCalled()
    expect(screen.queryByTestId("draft-notice")).not.toBeInTheDocument()
  })

  it("announces an uploaded session whose draft lands before the list shows it", async () => {
    serve([], { "session-9": "pending_review" })
    renderWithProviders(<DraftNotices />)

    act(() => watchDraft("session-9"))

    expect(await screen.findByTestId("draft-notice")).toHaveTextContent("Draft ready")
  })

  it("can be dismissed", async () => {
    const queryClient = await seenDrafting()
    serve([session("pending_review")], { "session-1": "pending_review" })
    await poll(queryClient)
    await screen.findByTestId("draft-notice")

    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }))

    expect(screen.queryByTestId("draft-notice")).not.toBeInTheDocument()
  })
})

describe("isWatchableDraft", () => {
  const now = Date.parse("2026-10-06T12:00:00Z")

  it("watches a session being drafted", () => {
    expect(
      isWatchableDraft({ status: "processing", created_at: "2026-10-06T11:55:00Z", updated_at: null }, now),
    ).toBe(true)
  })

  it("leaves a session that has sat mid-draft for hours", () => {
    expect(
      isWatchableDraft({ status: "processing", created_at: "2026-10-06T08:00:00Z", updated_at: null }, now),
    ).toBe(false)
  })

  it("leaves a session that is not being drafted", () => {
    expect(
      isWatchableDraft({ status: "finalized", created_at: "2026-10-06T11:55:00Z", updated_at: null }, now),
    ).toBe(false)
  })
})
