// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The read-aloud script: reachable when the practice asks clients about
 * AI-assisted notes, absent when it does not, and always reading the
 * practice's own audio retention window.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { AiNotesConsentScriptButton, consentScript, formatRetention } from "../AiNotesConsentScript"
import type { AiNotesConsentSetting } from "@/types/aiConsent"

const mockSetting = vi.fn()

vi.mock("@/hooks/useAiConsent", () => ({
  useAiNotesConsentSetting: () => mockSetting(),
}))

function given(setting: AiNotesConsentSetting | undefined) {
  mockSetting.mockReturnValue({ data: setting })
}

describe("formatRetention", () => {
  it.each([
    [365, "1 year"],
    [730, "2 years"],
    [90, "90 days"],
    [30, "30 days"],
    [400, "400 days"],
  ])("%i days reads as %s", (days, said) => {
    expect(formatRetention(days)).toBe(said)
  })
})

describe("consentScript", () => {
  it("covers recording, AI drafting, review, retention and saying no", () => {
    const text = consentScript(90).join(" ")
    expect(text).toContain("record our session")
    expect(text).toContain("AI tool uses it to draft my notes")
    expect(text).toContain("I read and correct every note myself")
    expect(text).toContain("kept for up to 90 days")
    expect(text).toContain("You can say no, now or at any time")
  })
})

describe("AiNotesConsentScriptButton", () => {
  beforeEach(() => vi.clearAllMocks())

  it("opens the script with the practice's retention window", async () => {
    given({ ask_clients_about_ai_notes: true, audio_retention_days: 90, can_change: true })

    render(<AiNotesConsentScriptButton />)
    await userEvent.click(screen.getByRole("button", { name: "Consent script" }))

    const script = screen.getByTestId("ai-notes-consent-script")
    expect(script).toHaveTextContent("The audio is kept for up to 90 days.")
  })

  it("is absent when the practice does not ask", () => {
    given({ ask_clients_about_ai_notes: false, audio_retention_days: 365, can_change: true })

    const { container } = render(<AiNotesConsentScriptButton />)

    expect(container).toBeEmptyDOMElement()
  })

  it("is absent until the setting has loaded", () => {
    given(undefined)

    const { container } = render(<AiNotesConsentScriptButton />)

    expect(container).toBeEmptyDOMElement()
  })
})
