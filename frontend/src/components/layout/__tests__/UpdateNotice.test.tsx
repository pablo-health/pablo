// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * UpdateNotice — shown to a practice owner or admin when the deployment can
 * update itself and a newer release is published; nothing at all otherwise.
 *
 * The API is mocked at the module: a 404 is how a deployment that cannot
 * update itself answers.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { UpdateNotice } from "../UpdateNotice"
import * as softwareUpdateApi from "@/lib/api/softwareUpdate"
import { ApiError } from "@/lib/api/client"
import { renderWithProviders } from "@/test/renderWithProviders"

vi.mock("@/lib/api/softwareUpdate")
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}))
vi.mock("@/lib/config", () => ({
  useConfig: () => ({ dataMode: "api" }),
}))

const NEWER: softwareUpdateApi.SoftwareUpdate = {
  current: "v1.9.0",
  latest: "v1.10.0",
  notes_url: "https://github.com/example/pablo/releases/tag/v1.10.0",
  available: true,
  status: null,
}

describe("UpdateNotice", () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it("renders nothing and asks nothing more when the endpoint 404s", async () => {
    vi.mocked(softwareUpdateApi.fetchSoftwareUpdate).mockRejectedValue(
      new ApiError("NOT_FOUND", "Not found", undefined, 404),
    )
    const { container } = renderWithProviders(<UpdateNotice isPracticeAdmin />)

    await waitFor(() => expect(softwareUpdateApi.fetchSoftwareUpdate).toHaveBeenCalledTimes(1))
    // Let any retry or follow-up call land before checking there was none.
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(container).toBeEmptyDOMElement()
    expect(softwareUpdateApi.fetchSoftwareUpdate).toHaveBeenCalledTimes(1)
    expect(softwareUpdateApi.fetchUpdateStatus).not.toHaveBeenCalled()
    expect(softwareUpdateApi.requestSoftwareUpdate).not.toHaveBeenCalled()
  })

  it("asks nothing for a clinician who is not an admin", async () => {
    const { container } = renderWithProviders(<UpdateNotice isPracticeAdmin={false} />)
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(container).toBeEmptyDOMElement()
    expect(softwareUpdateApi.fetchSoftwareUpdate).not.toHaveBeenCalled()
  })

  it("renders nothing when the running release is the latest", async () => {
    vi.mocked(softwareUpdateApi.fetchSoftwareUpdate).mockResolvedValue({
      ...NEWER,
      current: "v1.10.0",
      available: false,
    })
    const { container } = renderWithProviders(<UpdateNotice isPracticeAdmin />)
    await waitFor(() => expect(softwareUpdateApi.fetchSoftwareUpdate).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it("offers the newer release, and asks for it once confirmed", async () => {
    vi.mocked(softwareUpdateApi.fetchSoftwareUpdate).mockResolvedValue(NEWER)
    vi.mocked(softwareUpdateApi.requestSoftwareUpdate).mockResolvedValue({ requested: "v1.10.0" })
    const user = userEvent.setup()
    renderWithProviders(<UpdateNotice isPracticeAdmin />)

    expect(
      await screen.findByText("A new version of Pablo is available: v1.10.0"),
    ).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Release notes" })).toHaveAttribute(
      "href",
      NEWER.notes_url,
    )

    await user.click(screen.getByRole("button", { name: "Update now" }))
    expect(softwareUpdateApi.requestSoftwareUpdate).not.toHaveBeenCalled()
    expect(
      screen.getByText("Pablo will be unavailable for about a minute while it updates."),
    ).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Update now" }))
    expect(
      await screen.findByText("Updating. Pablo will be back in about a minute."),
    ).toBeInTheDocument()
    expect(softwareUpdateApi.requestSoftwareUpdate).toHaveBeenCalledTimes(1)
  })
})
