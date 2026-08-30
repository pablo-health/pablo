// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { SetupStepper } from "../SetupStepper"

const STEPS = [
  { id: "one", label: "One" },
  { id: "two", label: "Two" },
  { id: "three", label: "Three" },
]

describe("SetupStepper", () => {
  it("renders todo, current, and done states", () => {
    render(<SetupStepper steps={STEPS} activeIndex={1} onJump={vi.fn()} />)

    const done = screen.getByRole("button", { name: /one/i })
    const current = screen.getByRole("button", { name: /two/i })
    const todo = screen.getByRole("button", { name: /three/i })

    // Done step shows a checkmark instead of its number.
    expect(done.querySelector("svg")).toBeInTheDocument()
    expect(done).not.toHaveTextContent("1")

    // Current step shows its number, styled distinctly.
    expect(current).toHaveTextContent("2")
    expect(current.className).toContain("bg-primary-100")

    // Todo step shows its number and is muted.
    expect(todo).toHaveTextContent("3")
    expect(todo.className).toContain("text-muted-foreground")
  })

  it("does not fire onJump for an unreachable step and disables it", async () => {
    const user = userEvent.setup()
    const onJump = vi.fn()
    render(
      <SetupStepper
        steps={STEPS}
        activeIndex={0}
        onJump={onJump}
        reachable={() => false}
      />
    )

    const future = screen.getByRole("button", { name: /three/i })
    expect(future).toBeDisabled()

    await user.click(future)
    expect(onJump).not.toHaveBeenCalled()
  })

  it("allows jumping to a step marked reachable", async () => {
    const user = userEvent.setup()
    const onJump = vi.fn()
    render(
      <SetupStepper
        steps={STEPS}
        activeIndex={0}
        onJump={onJump}
        reachable={(i) => i === 2}
      />
    )

    const future = screen.getByRole("button", { name: /three/i })
    expect(future).not.toBeDisabled()

    await user.click(future)
    expect(onJump).toHaveBeenCalledWith(2)
  })

  it("always allows jumping backward to an already-visited step", async () => {
    const user = userEvent.setup()
    const onJump = vi.fn()
    render(<SetupStepper steps={STEPS} activeIndex={2} onJump={onJump} />)

    await user.click(screen.getByRole("button", { name: /one/i }))
    expect(onJump).toHaveBeenCalledWith(0)
  })
})
