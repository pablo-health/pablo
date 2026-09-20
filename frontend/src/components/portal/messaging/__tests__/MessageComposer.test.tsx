// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * MessageComposer: sends the body, holds still while a send is in
 * flight, clears only on success, and always carries the expectation
 * notice.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MessageComposer } from "../MessageComposer"
import { CRISIS_LINE, NOT_FOR_EMERGENCIES } from "../ExpectationNotice"

describe("MessageComposer", () => {
  it("renders the expectation notice with the composer", () => {
    render(<MessageComposer onSend={vi.fn()} sending={false} />)

    expect(screen.getByText(NOT_FOR_EMERGENCIES)).toBeTruthy()
    expect(screen.getByText(CRISIS_LINE)).toBeTruthy()
  })

  it("sends the trimmed body and clears on success", async () => {
    const onSend = vi.fn().mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<MessageComposer onSend={onSend} sending={false} />)

    const body = screen.getByTestId("portal-messaging-composer-body")
    await user.type(body, "  Could we move Thursday?  ")
    await user.click(screen.getByTestId("portal-messaging-composer-send"))

    expect(onSend).toHaveBeenCalledWith("Could we move Thursday?", [])
    await waitFor(() => expect((body as HTMLTextAreaElement).value).toBe(""))
  })

  it("keeps the draft when the send fails", async () => {
    const onSend = vi.fn().mockRejectedValue(new Error("nope"))
    const user = userEvent.setup()
    render(
      <MessageComposer onSend={onSend} sending={false} error="That didn't send." />,
    )

    const body = screen.getByTestId("portal-messaging-composer-body")
    await user.type(body, "Still here")
    await user.click(screen.getByTestId("portal-messaging-composer-send"))

    await waitFor(() => expect(onSend).toHaveBeenCalled())
    expect((body as HTMLTextAreaElement).value).toBe("Still here")
    expect(screen.getByTestId("portal-messaging-composer-error").textContent).toBe(
      "That didn't send.",
    )
  })

  it("disables send while a send is in flight", () => {
    render(<MessageComposer onSend={vi.fn()} sending={true} />)

    expect(
      (screen.getByTestId("portal-messaging-composer-send") as HTMLButtonElement)
        .disabled,
    ).toBe(true)
    expect(
      (screen.getByTestId("portal-messaging-composer-body") as HTMLTextAreaElement)
        .disabled,
    ).toBe(true)
  })

  it("will not send an empty body", async () => {
    const onSend = vi.fn()
    const user = userEvent.setup()
    render(<MessageComposer onSend={onSend} sending={false} />)

    await user.type(screen.getByTestId("portal-messaging-composer-body"), "   ")
    await user.click(screen.getByTestId("portal-messaging-composer-send"))

    expect(onSend).not.toHaveBeenCalled()
  })
})
