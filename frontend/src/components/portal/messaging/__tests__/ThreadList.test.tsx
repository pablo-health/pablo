// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ThreadList: rows, unread badges, and the empty state that carries the
 * expectation notice.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { PatientMessageThread } from "@/lib/api/patientMessages"
import { ThreadList } from "../ThreadList"
import { CRISIS_LINE, NOT_FOR_EMERGENCIES } from "../ExpectationNotice"

const threads: PatientMessageThread[] = [
  {
    id: "t1",
    subject: "Refill question",
    status: "open",
    created_at: "2026-09-01T15:00:00Z",
    last_message_at: "2026-09-03T15:00:00Z",
    unread_count: 2,
  },
  {
    id: "t2",
    subject: null,
    status: "open",
    created_at: "2026-08-20T15:00:00Z",
    last_message_at: "2026-08-21T15:00:00Z",
    unread_count: 0,
  },
]

describe("ThreadList", () => {
  it("renders a row per thread with the unread badge from the payload", () => {
    render(
      <ThreadList threads={threads} onOpenThread={vi.fn()} onStartThread={vi.fn()} />,
    )

    expect(screen.getByText("Refill question")).toBeTruthy()
    expect(screen.getByTestId("portal-messaging-unread-t1").textContent).toBe("2")
    // Zero unread is no badge, not a zero.
    expect(screen.queryByTestId("portal-messaging-unread-t2")).toBeNull()
  })

  it("falls back to a neutral label for a thread with no subject", () => {
    render(
      <ThreadList threads={threads} onOpenThread={vi.fn()} onStartThread={vi.fn()} />,
    )

    expect(screen.getByTestId("portal-messaging-thread-t2").textContent).toContain(
      "Message",
    )
  })

  it("opens the thread that was clicked", async () => {
    const onOpenThread = vi.fn()
    const user = userEvent.setup()
    render(
      <ThreadList
        threads={threads}
        onOpenThread={onOpenThread}
        onStartThread={vi.fn()}
      />,
    )

    await user.click(screen.getByTestId("portal-messaging-thread-t1"))

    expect(onOpenThread).toHaveBeenCalledWith("t1")
  })

  it("carries the expectation copy in the empty state", () => {
    render(<ThreadList threads={[]} onOpenThread={vi.fn()} onStartThread={vi.fn()} />)

    expect(screen.getByTestId("portal-messaging-thread-list-empty")).toBeTruthy()
    expect(screen.getByText(NOT_FOR_EMERGENCIES)).toBeTruthy()
    expect(screen.getByText(CRISIS_LINE)).toBeTruthy()
  })
})
