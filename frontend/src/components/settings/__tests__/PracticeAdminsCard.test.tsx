// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings > Profile: the practice owner choosing who else may change practice
 * settings. One switch per colleague; the owner has no row, because the owner
 * is always able to and cannot be switched off.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { PracticeAdminsCard } from "../PracticeAdminsCard"
import type { PracticeAdminsResponse } from "@/lib/api/practiceAdmins"

const mockAdmins = vi.fn()
const mockMutate = vi.fn()
const mockUpdate = vi.fn()

vi.mock("@/hooks/usePracticeAdmins", () => ({
  usePracticeAdmins: () => mockAdmins(),
  useSetPracticeAdmin: () => mockUpdate(),
}))

const OWNER = {
  user_id: "owner",
  name: "Olive Owner",
  email: "olive@example.com",
  is_owner: true,
  is_admin: true,
}

function given(data: PracticeAdminsResponse | undefined, update: Record<string, unknown> = {}) {
  mockAdmins.mockReturnValue({ data })
  mockUpdate.mockReturnValue({ mutate: mockMutate, isPending: false, isError: false, ...update })
}

describe("PracticeAdminsCard", () => {
  beforeEach(() => vi.clearAllMocks())

  it("lists colleagues, not the owner, with who is an admin", () => {
    given({
      clinicians: [
        OWNER,
        { user_id: "a", name: "Avery Admin", email: "avery@example.com", is_owner: false, is_admin: true },
        { user_id: "c", name: null, email: "casey@example.com", is_owner: false, is_admin: false },
      ],
    })

    render(<PracticeAdminsCard />)

    expect(screen.queryByText("Olive Owner")).toBeNull()
    expect(screen.getByRole("switch", { name: "Avery Admin is an admin" })).toHaveAttribute(
      "aria-checked",
      "true",
    )
    // No name on file: the address stands in, once.
    expect(screen.getByRole("switch", { name: "casey@example.com is an admin" })).toHaveAttribute(
      "aria-checked",
      "false",
    )
    expect(screen.getAllByText("casey@example.com")).toHaveLength(1)
  })

  it("grants and revokes with the colleague's id", async () => {
    given({
      clinicians: [
        OWNER,
        { user_id: "a", name: "Avery Admin", email: "avery@example.com", is_owner: false, is_admin: true },
        { user_id: "c", name: "Casey Colleague", email: "casey@example.com", is_owner: false, is_admin: false },
      ],
    })

    render(<PracticeAdminsCard />)
    await userEvent.click(screen.getByRole("switch", { name: "Casey Colleague is an admin" }))
    expect(mockMutate).toHaveBeenLastCalledWith({ userId: "c", isAdmin: true }, expect.anything())

    await userEvent.click(screen.getByRole("switch", { name: "Avery Admin is an admin" }))
    expect(mockMutate).toHaveBeenLastCalledWith({ userId: "a", isAdmin: false }, expect.anything())
  })

  it("says what will happen when the owner is the only clinician", () => {
    given({ clinicians: [OWNER] })

    render(<PracticeAdminsCard />)

    expect(
      screen.getByText("When another clinician joins your practice, you can make them an admin here."),
    ).toBeInTheDocument()
    expect(screen.queryByRole("switch")).toBeNull()
  })

  it("says so when a change does not save", () => {
    given(
      {
        clinicians: [
          OWNER,
          { user_id: "c", name: "Casey Colleague", email: "casey@example.com", is_owner: false, is_admin: false },
        ],
      },
      { isError: true },
    )

    render(<PracticeAdminsCard />)

    expect(screen.getByRole("alert")).toHaveTextContent("That didn't save. Try again.")
  })
})
