// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { InfoPopover } from "../InfoPopover"

function renderPopover() {
  render(
    <>
      <button type="button">Before</button>
      <InfoPopover label="About this permission">Google limits access.</InfoPopover>
      <button type="button">After</button>
    </>
  )
  return screen.getByRole("button", { name: "About this permission" })
}

describe("InfoPopover", () => {
  it("is closed until asked", () => {
    renderPopover()
    expect(screen.queryByText("Google limits access.")).not.toBeInTheDocument()
  })

  it("opens on a click and stays open for the rest of that press", async () => {
    const user = userEvent.setup()
    const button = renderPopover()
    await user.click(button)
    expect(screen.getByText("Google limits access.")).toBeInTheDocument()
    expect(button).toHaveAttribute("aria-expanded", "true")
  })

  it("opens when the button takes keyboard focus, and describes it", async () => {
    const user = userEvent.setup()
    const button = renderPopover()
    screen.getByRole("button", { name: "Before" }).focus()
    await user.tab()
    expect(button).toHaveFocus()
    const text = screen.getByText("Google limits access.")
    expect(button).toHaveAttribute("aria-describedby", text.id)
  })

  it("opens on Enter and on Space", async () => {
    const user = userEvent.setup()
    const button = renderPopover()
    // Focus without the focus handler opening it, as a browser that does not
    // focus on tap would leave it.
    fireEvent.click(button)
    await user.keyboard("{Escape}")
    expect(screen.queryByText("Google limits access.")).not.toBeInTheDocument()
    expect(button).toHaveFocus()

    await user.keyboard("{Enter}")
    expect(screen.getByText("Google limits access.")).toBeInTheDocument()
    await user.keyboard("{Escape}")
    await user.keyboard(" ")
    expect(screen.getByText("Google limits access.")).toBeInTheDocument()
  })

  it("closes on Escape and leaves focus on the button without reopening", async () => {
    const user = userEvent.setup()
    const button = renderPopover()
    await user.click(button)
    await user.keyboard("{Escape}")
    expect(screen.queryByText("Google limits access.")).not.toBeInTheDocument()
    expect(button).toHaveFocus()
  })

  it("closes when focus moves on", async () => {
    const user = userEvent.setup()
    renderPopover()
    screen.getByRole("button", { name: "Before" }).focus()
    await user.tab()
    expect(screen.getByText("Google limits access.")).toBeInTheDocument()
    await user.tab()
    expect(screen.getByRole("button", { name: "After" })).toHaveFocus()
    expect(screen.queryByText("Google limits access.")).not.toBeInTheDocument()
  })
})
