// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { AudioRetentionSettings } from "../AudioRetentionSettings"
import * as practicesApi from "@/lib/api/practices"
import { ApiError } from "@/lib/api/client"

vi.mock("@/lib/api/practices", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/practices")>()
  return {
    ...actual,
    getAudioRetention: vi.fn(),
    updateAudioRetention: vi.fn(),
  }
})
vi.mock("@/lib/config", () => ({
  useConfig: () => ({ dataMode: "api" }),
}))
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ loading: false }),
}))

function given(days: number) {
  vi.mocked(practicesApi.getAudioRetention).mockResolvedValue({
    practice_id: "prac_1",
    audio_retention_days: days,
  })
}

function renderIt() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <AudioRetentionSettings />
    </QueryClientProvider>,
  )
}

const onSigning = () => screen.findByRole("radio", { name: "Delete when the note is signed" })
const afterDays = () => screen.getByRole("radio", { name: "Delete after" })
const daysBox = () => screen.getByRole("spinbutton", { name: "Days to keep session audio" })

describe("AudioRetentionSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("shows a practice that keeps audio for days", async () => {
    given(365)
    renderIt()

    expect(await onSigning()).not.toBeChecked()
    expect(afterDays()).toBeChecked()
    expect(daysBox()).toHaveValue(365)
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
  })

  it("shows a practice that deletes when the note is signed", async () => {
    given(0)
    renderIt()

    expect(await onSigning()).toBeChecked()
    expect(afterDays()).not.toBeChecked()
  })

  it("saves 0 when the clinician chooses to delete on signing", async () => {
    const user = userEvent.setup()
    given(365)
    vi.mocked(practicesApi.updateAudioRetention).mockResolvedValue({
      practice_id: "prac_1",
      audio_retention_days: 0,
    })
    renderIt()

    await user.click(await onSigning())
    await user.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => {
      expect(practicesApi.updateAudioRetention).toHaveBeenCalledWith(0, undefined)
    })
    expect(await screen.findByRole("status")).toHaveTextContent("Saved")
  })

  it("saves a number of days", async () => {
    const user = userEvent.setup()
    given(0)
    vi.mocked(practicesApi.updateAudioRetention).mockResolvedValue({
      practice_id: "prac_1",
      audio_retention_days: 30,
    })
    renderIt()

    await onSigning()
    await user.clear(daysBox())
    await user.type(daysBox(), "30")
    expect(afterDays()).toBeChecked()
    await user.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => {
      expect(practicesApi.updateAudioRetention).toHaveBeenCalledWith(30, undefined)
    })
  })

  it.each(["0", "2556", "1.5", ""])("refuses %j days", async (typed) => {
    const user = userEvent.setup()
    given(365)
    renderIt()

    await onSigning()
    await user.clear(daysBox())
    if (typed) await user.type(daysBox(), typed)

    expect(screen.getByRole("alert")).toHaveTextContent("Enter a number of days from 1 to 2555.")
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
  })

  it("shows the error when saving fails", async () => {
    const user = userEvent.setup()
    given(365)
    vi.mocked(practicesApi.updateAudioRetention).mockRejectedValue(new Error("network down"))
    renderIt()

    await user.click(await onSigning())
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(/network down/i)
  })

  it("tells someone who isn't the owner who can change it", async () => {
    vi.mocked(practicesApi.getAudioRetention).mockRejectedValue(
      new ApiError("NOT_PRACTICE_OWNER", "Only the practice owner", undefined, 403),
    )
    renderIt()

    expect(await screen.findByText("Only the practice owner can change this.")).toBeInTheDocument()
    expect(screen.queryByRole("radio")).not.toBeInTheDocument()
  })
})
