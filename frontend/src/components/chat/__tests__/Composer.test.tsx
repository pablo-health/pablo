// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Composer (§13.10) Component Tests
 *
 * Covers the token-budget meter's basic shape plus — the main focus —
 * that read-only deployment mode disables the message box and Send
 * button, shows an explanatory note, and blocks submission via Enter.
 */

import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { Composer } from "../Composer"

describe("Composer", () => {
  it("renders an enabled textarea and Send button by default", () => {
    render(<Composer contextTokens={0} tokenBudget={1000} onSend={vi.fn()} />)

    expect(screen.getByRole("textbox", { name: "Message" })).not.toBeDisabled()
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument()
  })

  it("calls onSend with the trimmed message on Enter and clears the box", () => {
    const onSend = vi.fn()
    render(<Composer contextTokens={0} tokenBudget={1000} onSend={onSend} />)

    const textarea = screen.getByRole("textbox", { name: "Message" })
    fireEvent.change(textarea, { target: { value: "  Hello there  " } })
    fireEvent.keyDown(textarea, { key: "Enter" })

    expect(onSend).toHaveBeenCalledWith("Hello there")
    expect(textarea).toHaveValue("")
  })

  it("does not call onSend for an empty or whitespace-only message", () => {
    const onSend = vi.fn()
    render(<Composer contextTokens={0} tokenBudget={1000} onSend={onSend} />)

    const textarea = screen.getByRole("textbox", { name: "Message" })
    fireEvent.change(textarea, { target: { value: "   " } })
    fireEvent.keyDown(textarea, { key: "Enter" })

    expect(onSend).not.toHaveBeenCalled()
  })

  describe("read-only deployment mode", () => {
    afterEach(() => vi.unstubAllEnvs())

    it("disables the textarea and Send button, and shows a read-only note", () => {
      vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")

      render(<Composer contextTokens={0} tokenBudget={1000} onSend={vi.fn()} />)

      expect(screen.getByRole("textbox", { name: "Message" })).toBeDisabled()
      expect(screen.getByRole("button", { name: "Send" })).toBeDisabled()
      expect(screen.getByText(/read-only/i)).toBeInTheDocument()
    })

    it("does not show the placeholder text when read-only", () => {
      vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")

      render(
        <Composer
          contextTokens={0}
          tokenBudget={1000}
          placeholder="Ask a question…"
          onSend={vi.fn()}
        />,
      )

      const textarea = screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement
      expect(textarea.placeholder).toBe("")
    })

    it("does not call onSend when typing and pressing Enter", () => {
      vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")
      const onSend = vi.fn()

      render(<Composer contextTokens={0} tokenBudget={1000} onSend={onSend} />)

      const textarea = screen.getByRole("textbox", { name: "Message" })
      fireEvent.change(textarea, { target: { value: "Can we schedule a follow-up?" } })
      fireEvent.keyDown(textarea, { key: "Enter" })

      expect(onSend).not.toHaveBeenCalled()
    })

    it("shows the enabled textarea, placeholder and no read-only note when the deployment flag is unset", () => {
      render(
        <Composer
          contextTokens={0}
          tokenBudget={1000}
          placeholder="Ask a question…"
          onSend={vi.fn()}
        />,
      )

      const textarea = screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement
      expect(textarea).not.toBeDisabled()
      expect(textarea.placeholder).toBe("Ask a question…")
      fireEvent.change(textarea, { target: { value: "Hi" } })
      expect(screen.getByRole("button", { name: "Send" })).not.toBeDisabled()
      expect(screen.queryByText(/read-only/i)).not.toBeInTheDocument()
    })

    it("calls onSend when typing and pressing Enter", () => {
      const onSend = vi.fn()

      render(<Composer contextTokens={0} tokenBudget={1000} onSend={onSend} />)

      const textarea = screen.getByRole("textbox", { name: "Message" })
      fireEvent.change(textarea, { target: { value: "Can we schedule a follow-up?" } })
      fireEvent.keyDown(textarea, { key: "Enter" })

      expect(onSend).toHaveBeenCalledWith("Can we schedule a follow-up?")
    })
  })
})
