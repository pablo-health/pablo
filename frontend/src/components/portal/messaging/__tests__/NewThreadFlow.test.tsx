// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * NewThreadFlow: the optional subject, the first message, and the
 * expectation notice a patient sees before writing anything at all.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { NewThreadFlow } from "../NewThreadFlow"
import { CRISIS_LINE, NOT_FOR_EMERGENCIES } from "../ExpectationNotice"

describe("NewThreadFlow", () => {
  it("renders the expectation notice", () => {
    render(<NewThreadFlow onStart={vi.fn()} starting={false} />)

    expect(screen.getByText(NOT_FOR_EMERGENCIES)).toBeTruthy()
    expect(screen.getByText(CRISIS_LINE)).toBeTruthy()
  })

  it("starts a thread with the subject and the body", async () => {
    const onStart = vi.fn().mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<NewThreadFlow onStart={onStart} starting={false} />)

    await user.type(
      screen.getByTestId("portal-messaging-new-thread-subject"),
      "Scheduling",
    )
    await user.type(
      screen.getByTestId("portal-messaging-new-thread-body"),
      "Could we move Thursday?",
    )
    await user.click(screen.getByTestId("portal-messaging-new-thread-send"))

    expect(onStart).toHaveBeenCalledWith({
      subject: "Scheduling",
      body: "Could we move Thursday?",
    })
  })

  it("sends a null subject when the patient leaves it blank", async () => {
    const onStart = vi.fn().mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<NewThreadFlow onStart={onStart} starting={false} />)

    await user.type(screen.getByTestId("portal-messaging-new-thread-body"), "Hello")
    await user.click(screen.getByTestId("portal-messaging-new-thread-send"))

    expect(onStart).toHaveBeenCalledWith({ subject: null, body: "Hello" })
  })

  it("holds still while the first message is in flight", () => {
    render(<NewThreadFlow onStart={vi.fn()} starting={true} />)

    expect(
      (screen.getByTestId("portal-messaging-new-thread-send") as HTMLButtonElement)
        .disabled,
    ).toBe(true)
  })

  it("keeps the draft when starting fails", async () => {
    const onStart = vi.fn().mockRejectedValue(new Error("nope"))
    const user = userEvent.setup()
    render(<NewThreadFlow onStart={onStart} starting={false} />)

    const body = screen.getByTestId("portal-messaging-new-thread-body")
    await user.type(body, "Still here")
    await user.click(screen.getByTestId("portal-messaging-new-thread-send"))

    await waitFor(() => expect(onStart).toHaveBeenCalled())
    expect((body as HTMLTextAreaElement).value).toBe("Still here")
  })
})
