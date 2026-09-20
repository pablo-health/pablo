// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PortalMessaging: the whole surface over a mocked client — list, start
 * a thread, read it, reply, and come back to a list that has caught up.
 *
 * Also pins the two things the notice must survive: a deployment that
 * does not serve reply-time settings at all, and one that does.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type {
  PatientMessageThread,
  PatientMessageThreadDetail,
} from "@/lib/api/patientMessages"
import * as api from "@/lib/api/patientMessages"
import { PortalMessaging } from "../PortalMessaging"
import { DEFAULT_RESPONSE_TIME } from "../ExpectationNotice"

vi.mock("@/lib/api/patientMessages")

const TOKEN = "session-token"

const newThread: PatientMessageThread = {
  id: "t1",
  subject: "Scheduling",
  status: "open",
  created_at: "2026-09-03T15:00:00Z",
  last_message_at: "2026-09-03T15:00:00Z",
  unread_count: 0,
}

const newThreadDetail: PatientMessageThreadDetail = {
  ...newThread,
  messages: [
    {
      id: "m1",
      thread_id: "t1",
      sender: "patient",
      body: "Could we move Thursday?",
      created_at: "2026-09-03T15:00:00Z",
    },
  ],
}

function renderSurface() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "PortalMessagingWrapper"
  return render(<PortalMessaging sessionToken={TOKEN} />, { wrapper: Wrapper })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.listThreads).mockResolvedValue({ data: [], total: 0 })
  vi.mocked(api.getMessagingSettings).mockResolvedValue(null)
  vi.mocked(api.getThread).mockResolvedValue(newThreadDetail)
  vi.mocked(api.startThread).mockResolvedValue(newThreadDetail)
  vi.mocked(api.markThreadRead).mockResolvedValue({ marked_read: 0 })
  vi.mocked(api.sendMessage).mockResolvedValue(newThreadDetail.messages[0])
})

describe("PortalMessaging", () => {
  it("renders the shipped default when the settings route is not served", async () => {
    renderSurface()

    await screen.findByTestId("portal-messaging-thread-list-empty")
    expect(screen.getByTestId("portal-messaging-response-time").textContent).toBe(
      DEFAULT_RESPONSE_TIME,
    )
  })

  it("renders the configured reply time when the practice has set one", async () => {
    vi.mocked(api.getMessagingSettings).mockResolvedValue({
      sla_text: "We reply on Tuesdays and Thursdays.",
    })

    renderSurface()

    await waitFor(() =>
      expect(screen.getByTestId("portal-messaging-response-time").textContent).toBe(
        "We reply on Tuesdays and Thursdays.",
      ),
    )
  })

  it("falls back to the default when settings cannot be fetched at all", async () => {
    vi.mocked(api.getMessagingSettings).mockRejectedValue(new Error("boom"))

    renderSurface()

    await screen.findByTestId("portal-messaging-thread-list-empty")
    expect(screen.getByTestId("portal-messaging-response-time").textContent).toBe(
      DEFAULT_RESPONSE_TIME,
    )
  })

  it("starts a thread, opens it, and the list catches up", async () => {
    vi.mocked(api.listThreads)
      .mockResolvedValueOnce({ data: [], total: 0 })
      .mockResolvedValue({ data: [newThread], total: 1 })
    const user = userEvent.setup()
    renderSurface()

    await user.click(await screen.findByTestId("portal-messaging-start-thread"))
    await user.type(
      screen.getByTestId("portal-messaging-new-thread-subject"),
      "Scheduling",
    )
    await user.type(
      screen.getByTestId("portal-messaging-new-thread-body"),
      "Could we move Thursday?",
    )
    await user.click(screen.getByTestId("portal-messaging-new-thread-send"))

    expect(api.startThread).toHaveBeenCalledWith(TOKEN, {
      subject: "Scheduling",
      body: "Could we move Thursday?",
    })

    // The thread it created is the thread it opens.
    await screen.findByTestId("portal-messaging-thread-view")
    await waitFor(() => expect(api.markThreadRead).toHaveBeenCalledWith(TOKEN, "t1"))

    await user.click(screen.getByTestId("portal-messaging-back"))
    expect(await screen.findByTestId("portal-messaging-thread-t1")).toBeTruthy()
  })

  it("sends a reply into the open thread", async () => {
    vi.mocked(api.listThreads).mockResolvedValue({ data: [newThread], total: 1 })
    const user = userEvent.setup()
    renderSurface()

    await user.click(await screen.findByTestId("portal-messaging-thread-t1"))
    await screen.findByTestId("portal-messaging-thread-view")
    await user.type(
      screen.getByTestId("portal-messaging-composer-body"),
      "Thursday works",
    )
    await user.click(screen.getByTestId("portal-messaging-composer-send"))

    await waitFor(() =>
      expect(api.sendMessage).toHaveBeenCalledWith(TOKEN, "t1", "Thursday works", []),
    )
  })

  it("says so plainly when the list cannot be loaded", async () => {
    vi.mocked(api.listThreads).mockRejectedValue(new Error("boom"))

    renderSurface()

    expect((await screen.findByTestId("portal-messaging-error")).textContent).toContain(
      "Messages aren't loading",
    )
  })
})
