// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings > Sessions & recording > Video sessions: whether clients are sent
 * their video link. On by default; the owner turns it off and on, anyone else
 * sees it and cannot change it, and a deployment that sends no link does not
 * show it at all.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { ClientVideoLinkSetting } from "../ClientVideoLinkSetting"
import type { ClientVideoLinkSetting as Setting } from "@/lib/api/clientVideoLink"

const mockSetting = vi.fn()
const mockMutate = vi.fn()
const mockUpdate = vi.fn()

vi.mock("@/hooks/useClientVideoLink", () => ({
  useClientVideoLinkSetting: () => mockSetting(),
  useUpdateClientVideoLinkSetting: () => mockUpdate(),
}))

function given(setting: Setting | undefined, update = { isPending: false, isError: false }) {
  mockSetting.mockReturnValue({ data: setting })
  mockUpdate.mockReturnValue({ mutate: mockMutate, ...update })
}

const LABEL = "Send clients their video link"

describe("ClientVideoLinkSetting", () => {
  beforeEach(() => vi.clearAllMocks())

  it("is on, and the owner turns it off", async () => {
    given({ send_clients_video_link: true, available: true, can_change: true })

    render(<ClientVideoLinkSetting />)
    const toggle = screen.getByRole("switch", { name: LABEL })
    expect(toggle).toHaveAttribute("aria-checked", "true")
    expect(screen.getByText("Adds the link to join to their appointment reminders.")).toBeInTheDocument()

    await userEvent.click(toggle)
    expect(mockMutate).toHaveBeenCalledWith(false, expect.anything())
  })

  it("the owner turns it back on", async () => {
    given({ send_clients_video_link: false, available: true, can_change: true })

    render(<ClientVideoLinkSetting />)
    const toggle = screen.getByRole("switch", { name: LABEL })
    expect(toggle).toHaveAttribute("aria-checked", "false")

    await userEvent.click(toggle)
    expect(mockMutate).toHaveBeenCalledWith(true, expect.anything())
  })

  it("someone other than the owner cannot change it", () => {
    given({ send_clients_video_link: true, available: true, can_change: false })

    render(<ClientVideoLinkSetting />)

    expect(screen.getByRole("switch", { name: LABEL })).toBeDisabled()
    expect(screen.getByText("Only the practice owner can change this.")).toBeInTheDocument()
  })

  it("is not shown when the deployment sends no link", () => {
    given({ send_clients_video_link: true, available: false, can_change: true })

    render(<ClientVideoLinkSetting />)

    expect(screen.queryByRole("switch")).not.toBeInTheDocument()
  })

  it("is not shown before the setting has loaded", () => {
    given(undefined)

    render(<ClientVideoLinkSetting />)

    expect(screen.queryByRole("switch")).not.toBeInTheDocument()
  })

  it("says when a change did not save", () => {
    given(
      { send_clients_video_link: true, available: true, can_change: true },
      { isPending: false, isError: true },
    )

    render(<ClientVideoLinkSetting />)

    expect(screen.getByRole("alert")).toHaveTextContent("That didn't save. Try again.")
  })
})
