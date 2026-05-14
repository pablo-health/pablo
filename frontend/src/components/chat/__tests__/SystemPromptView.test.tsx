// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SystemPromptView (§13.6) — chevron + "i" affordance that expands a
 * read-only disclosure of the verbatim caller_system_prompt.
 */

import { describe, it, expect } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"

import { SystemPromptView } from "../SystemPromptView"

const SAMPLE_PROMPT =
  "You are a session-prep assistant. Answer concisely.\nCite sources."

describe("SystemPromptView", () => {
  it("is closed by default — the verbatim prompt text is not rendered", () => {
    render(
      <SystemPromptView
        callerFeatureKey="session_prep"
        systemPrompt={SAMPLE_PROMPT}
      />,
    )
    expect(document.querySelector("[data-slot='chat-system-prompt-body']")).toBeNull()
    expect(screen.getByRole("button", { name: /(show|hide) system prompt/i }))
      .toHaveAttribute("aria-expanded", "false")
  })

  it("expands on click and reveals the caller_system_prompt verbatim", () => {
    render(
      <SystemPromptView
        callerFeatureKey="session_prep"
        systemPrompt={SAMPLE_PROMPT}
      />,
    )
    const toggle = screen.getByRole("button", { name: /(show|hide) system prompt/i })
    fireEvent.click(toggle)

    // Region present + verbatim text rendered
    expect(toggle).toHaveAttribute("aria-expanded", "true")
    expect(
      document.querySelector("[data-slot='chat-system-prompt-body']")?.textContent,
    ).toBe(SAMPLE_PROMPT)
    // Caller feature key visible in the "Using the … prompt:" line
    expect(screen.getByText("session_prep")).toBeInTheDocument()
    expect(screen.getByText(/using the/i).textContent).toMatch(/prompt:/i)
  })

  it("collapses again on second click and hides the prompt text", () => {
    render(
      <SystemPromptView
        callerFeatureKey="session_prep"
        systemPrompt={SAMPLE_PROMPT}
      />,
    )
    const toggle = screen.getByRole("button", { name: /(show|hide) system prompt/i })
    fireEvent.click(toggle)
    expect(
      document.querySelector("[data-slot='chat-system-prompt-body']")?.textContent,
    ).toBe(SAMPLE_PROMPT)

    fireEvent.click(toggle)
    expect(document.querySelector("[data-slot='chat-system-prompt-body']")).toBeNull()
    expect(toggle).toHaveAttribute("aria-expanded", "false")
  })

  it("exposes the prompt body as a <pre> so whitespace is preserved", () => {
    render(
      <SystemPromptView
        callerFeatureKey="session_prep"
        systemPrompt={SAMPLE_PROMPT}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: /(show|hide) system prompt/i }))
    const body = document.querySelector(
      "[data-slot='chat-system-prompt-body']",
    )
    expect(body?.tagName.toLowerCase()).toBe("pre")
  })

  it("renders no edit affordance (read-only)", () => {
    render(
      <SystemPromptView
        callerFeatureKey="session_prep"
        systemPrompt={SAMPLE_PROMPT}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: /(show|hide) system prompt/i }))
    // No textarea, no input, no contenteditable surface inside the region
    const region = document.querySelector(
      "[data-slot='chat-system-prompt-region']",
    )
    expect(region?.querySelector("textarea")).toBeNull()
    expect(region?.querySelector("input")).toBeNull()
    expect(region?.querySelector("[contenteditable]")).toBeNull()
  })
})
