// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings > Sessions & recording: whether the practice asks clients about
 * AI-assisted notes. The owner turns it on and off; anyone else sees it and
 * cannot change it.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { AiNotesConsentCard } from "../AiNotesConsentCard"
import type { AiNotesConsentSetting } from "@/types/aiConsent"

const mockSetting = vi.fn()
const mockMutate = vi.fn()

vi.mock("@/hooks/useAiConsent", () => ({
  useAiNotesConsentSetting: () => mockSetting(),
  useUpdateAiNotesConsentSetting: () => ({ mutate: mockMutate, isPending: false, isError: false }),
}))

function given(setting: AiNotesConsentSetting | undefined) {
  mockSetting.mockReturnValue({ data: setting })
}

const LABEL = "Ask clients to agree to AI-assisted notes"

describe("AiNotesConsentCard", () => {
  beforeEach(() => vi.clearAllMocks())

  it("shows the setting on, and the owner turns it off", async () => {
    given({ ask_clients_about_ai_notes: true, audio_retention_days: 365, can_change: true })

    render(<AiNotesConsentCard />)
    const toggle = screen.getByRole("switch", { name: LABEL })
    expect(toggle).toHaveAttribute("aria-checked", "true")

    await userEvent.click(toggle)
    expect(mockMutate).toHaveBeenCalledWith(false, expect.anything())
  })

  it("the owner turns it back on", async () => {
    given({ ask_clients_about_ai_notes: false, audio_retention_days: 365, can_change: true })

    render(<AiNotesConsentCard />)
    const toggle = screen.getByRole("switch", { name: LABEL })
    expect(toggle).toHaveAttribute("aria-checked", "false")

    await userEvent.click(toggle)
    expect(mockMutate).toHaveBeenCalledWith(true, expect.anything())
  })

  it("someone other than the owner cannot change it", () => {
    given({ ask_clients_about_ai_notes: true, audio_retention_days: 365, can_change: false })

    render(<AiNotesConsentCard />)

    expect(screen.getByRole("switch", { name: LABEL })).toBeDisabled()
    expect(screen.getByText("Only the practice owner can change this.")).toBeInTheDocument()
  })
})
