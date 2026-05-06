// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { AudioRetentionSettings } from "../AudioRetentionSettings"
import * as practicesApi from "@/lib/api/practices"
import { ApiError } from "@/lib/api/client"

function moveSliderTo(slider: HTMLInputElement, value: number) {
  fireEvent.change(slider, { target: { value: String(value) } })
}

vi.mock("@/lib/api/practices", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/practices")>()
  return {
    ...actual,
    updateAudioRetention: vi.fn(),
  }
})
vi.mock("@/lib/config", () => ({
  useConfig: () => ({ dataMode: "api" }),
}))

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  )
}

describe("AudioRetentionSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders the slider with the default value when none provided", () => {
    renderWithClient(<AudioRetentionSettings practiceId="prac_1" />)

    const slider = screen.getByLabelText("Retention window") as HTMLInputElement
    expect(slider.type).toBe("range")
    expect(slider.min).toBe("30")
    expect(slider.max).toBe("2555")
    expect(slider.value).toBe("365")
    expect(screen.getByTestId("audio-retention-value")).toHaveTextContent(
      "365 days",
    )
  })

  it("uses the initial value when supplied", () => {
    renderWithClient(
      <AudioRetentionSettings practiceId="prac_1" initialDays={1000} />,
    )

    expect(screen.getByTestId("audio-retention-value")).toHaveTextContent(
      "1000 days",
    )
  })

  it("renders BAA-aligned helper copy with the live slider value", async () => {
    renderWithClient(
      <AudioRetentionSettings practiceId="prac_1" initialDays={365} />,
    )

    expect(
      screen.getByText(
        "Recordings older than 365 days are deleted nightly; each deletion writes an audit log row.",
      ),
    ).toBeInTheDocument()

    const slider = screen.getByLabelText("Retention window") as HTMLInputElement
    moveSliderTo(slider, 500)

    await waitFor(() => {
      expect(screen.getByTestId("audio-retention-value")).toHaveTextContent(
        "500 days",
      )
      expect(
        screen.getByText(
          "Recordings older than 500 days are deleted nightly; each deletion writes an audit log row.",
        ),
      ).toBeInTheDocument()
    })
  })

  it("save button is disabled when slider has not changed", () => {
    renderWithClient(
      <AudioRetentionSettings practiceId="prac_1" initialDays={365} />,
    )

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
  })

  it("calls the API and shows confirmation on successful save", async () => {
    const user = userEvent.setup()
    vi.mocked(practicesApi.updateAudioRetention).mockResolvedValue({
      practice_id: "prac_1",
      audio_retention_days: 370,
    })

    renderWithClient(
      <AudioRetentionSettings practiceId="prac_1" initialDays={365} />,
    )

    const slider = screen.getByLabelText("Retention window") as HTMLInputElement
    moveSliderTo(slider, 370)
    await waitFor(() =>
      expect(screen.getByTestId("audio-retention-value")).toHaveTextContent(
        "370 days",
      ),
    )

    const saveButton = screen.getByRole("button", { name: "Save" })
    expect(saveButton).toBeEnabled()
    await user.click(saveButton)

    await waitFor(() => {
      expect(practicesApi.updateAudioRetention).toHaveBeenCalledWith(
        "prac_1",
        370,
        undefined,
      )
    })

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("Saved")
    })
  })

  it("shows a range error message on a 400 response", async () => {
    const user = userEvent.setup()
    vi.mocked(practicesApi.updateAudioRetention).mockRejectedValue(
      new ApiError("BAD_REQUEST", "out of range", undefined, 400),
    )

    renderWithClient(
      <AudioRetentionSettings practiceId="prac_1" initialDays={365} />,
    )

    const slider = screen.getByLabelText("Retention window") as HTMLInputElement
    moveSliderTo(slider, 366)
    await user.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        /must be between 30 and 2555/i,
      )
    })
  })

  it("shows a generic error message on unexpected failures", async () => {
    const user = userEvent.setup()
    vi.mocked(practicesApi.updateAudioRetention).mockRejectedValue(
      new Error("network down"),
    )

    renderWithClient(
      <AudioRetentionSettings practiceId="prac_1" initialDays={365} />,
    )

    const slider = screen.getByLabelText("Retention window") as HTMLInputElement
    moveSliderTo(slider, 366)
    await user.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/network down/i)
    })
  })

  it("shows a saving state while the mutation is in flight", async () => {
    const user = userEvent.setup()
    let resolveFn: (v: { practice_id: string; audio_retention_days: number }) => void = () => {}
    vi.mocked(practicesApi.updateAudioRetention).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveFn = resolve
        }),
    )

    renderWithClient(
      <AudioRetentionSettings practiceId="prac_1" initialDays={365} />,
    )

    const slider = screen.getByLabelText("Retention window") as HTMLInputElement
    moveSliderTo(slider, 366)
    await user.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /saving/i }),
      ).toBeDisabled()
    })
    expect(slider).toBeDisabled()

    resolveFn({ practice_id: "prac_1", audio_retention_days: 366 })
  })
})
