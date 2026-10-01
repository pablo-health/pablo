// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Practice name — editable by the practice owner, read-only for everyone
 * else. The server refuses a non-owner's rename too; this pins that the
 * screen never offers one.
 */

import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { PracticeNameSettings } from "../PracticeNameSettings"

const mockMutate = vi.fn()
let isError = false

vi.mock("@/hooks/useProfessionalInfo", () => ({
  useUpdateProfessionalInfo: () => ({
    mutate: mockMutate,
    isPending: false,
    isError,
  }),
}))

describe("PracticeNameSettings", () => {
  beforeEach(() => {
    mockMutate.mockReset()
    isError = false
  })

  it("lets the owner rename the practice, sending the trimmed name", async () => {
    const user = userEvent.setup()
    render(<PracticeNameSettings currentName="Jane Doe" canEdit />)

    const input = screen.getByLabelText("Practice name")
    await user.clear(input)
    await user.type(input, "  Center for Wellness  ")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mockMutate).toHaveBeenCalledTimes(1)
    expect(mockMutate.mock.calls[0][0]).toEqual({ practice_name: "Center for Wellness" })
  })

  it("offers no save until the name changes, and none for a blank name", async () => {
    const user = userEvent.setup()
    render(<PracticeNameSettings currentName="Jane Doe" canEdit />)

    expect(screen.queryByRole("button", { name: "Save" })).toBeNull()

    const input = screen.getByLabelText("Practice name")
    await user.clear(input)
    await user.type(input, "   ")
    const save = screen.getByRole("button", { name: "Save" })
    expect(save).toBeDisabled()
  })

  it("shows the name read-only to someone who is not the owner", async () => {
    const user = userEvent.setup()
    render(<PracticeNameSettings currentName="Jane Doe" canEdit={false} />)

    const input = screen.getByLabelText("Practice name")
    expect(input).toHaveAttribute("readonly")
    expect(screen.getByText("Only the practice owner can change this.")).toBeInTheDocument()

    await user.type(input, "x")
    expect(input).toHaveValue("Jane Doe")
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull()
  })

  it("says so when the save fails", () => {
    isError = true
    render(<PracticeNameSettings currentName="Jane Doe" canEdit />)
    expect(screen.getByRole("alert")).toHaveTextContent("The practice name could not be saved.")
  })
})
