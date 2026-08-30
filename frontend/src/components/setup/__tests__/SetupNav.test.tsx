// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { SetupNav } from "../SetupNav"

describe("SetupNav", () => {
  it("disables Continue while the gate is unmet, and enables it once met", () => {
    const { rerender } = render(
      <SetupNav onContinue={vi.fn()} canContinue={false} isLastStep={false} />
    )
    expect(screen.getByRole("button", { name: /continue/i })).toBeDisabled()

    rerender(<SetupNav onContinue={vi.fn()} canContinue={true} isLastStep={false} />)
    expect(screen.getByRole("button", { name: /continue/i })).not.toBeDisabled()
  })

  it("does not fire onContinue while disabled", async () => {
    const user = userEvent.setup()
    const onContinue = vi.fn()
    render(<SetupNav onContinue={onContinue} canContinue={false} isLastStep={false} />)

    await user.click(screen.getByRole("button", { name: /continue/i }))
    expect(onContinue).not.toHaveBeenCalled()
  })

  it("reads Finish on the last step", () => {
    render(<SetupNav onContinue={vi.fn()} canContinue={true} isLastStep={true} />)
    expect(screen.getByRole("button", { name: /finish/i })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /^continue/i })).not.toBeInTheDocument()
  })

  it("omits Back on the first step and Skip when not skippable", () => {
    render(<SetupNav onContinue={vi.fn()} canContinue={true} isLastStep={false} />)
    expect(screen.queryByRole("button", { name: /back/i })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /skip/i })).not.toBeInTheDocument()
  })

  it("renders Back and Skip when provided, and fires their callbacks", async () => {
    const user = userEvent.setup()
    const onBack = vi.fn()
    const onSkip = vi.fn()
    render(
      <SetupNav
        onBack={onBack}
        onSkip={onSkip}
        onContinue={vi.fn()}
        canContinue={true}
        isLastStep={false}
      />
    )

    await user.click(screen.getByRole("button", { name: /back/i }))
    expect(onBack).toHaveBeenCalledOnce()

    await user.click(screen.getByRole("button", { name: /skip/i }))
    expect(onSkip).toHaveBeenCalledOnce()
  })
})
