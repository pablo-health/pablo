// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The first screen of setup, which is the only question every therapist
 * answers. What is worth guarding here is that it stays a question about
 * today, that choosing moves her on, and that a mis-click is recoverable.
 */

import { fireEvent, render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { GetPaidWizard } from "../GetPaidWizard"

describe("the first screen", () => {
  it("asks how she is paid today, not what she wants", () => {
    // Situational, not aspirational: the routing depends on what is true, and
    // a question about wishes invites an answer about the next six months.
    render(<GetPaidWizard />)

    expect(
      screen.getByText("How do you get paid today?"),
    ).toBeInTheDocument()
    expect(screen.queryByText(/wish|would you like|do you want to/i)).not.toBeInTheDocument()
  })

  it("offers the four situations, none of which is a yes/no about credentialing", () => {
    render(<GetPaidWizard />)

    const options = screen.getAllByRole("button", { pressed: false })
    expect(options).toHaveLength(4)
    expect(screen.getByText("My clients pay me directly")).toBeInTheDocument()
    expect(screen.getByText("I’m already on insurance panels")).toBeInTheDocument()
  })

  it("moves her on as soon as she picks one", () => {
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByText("My clients pay me directly"))

    expect(
      screen.queryByText("How do you get paid today?"),
    ).not.toBeInTheDocument()
  })

  it("lets her back out of a mis-click with the answer still selected", () => {
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByText("I’m already on insurance panels"))
    fireEvent.click(screen.getByRole("button", { name: "Back" }))

    expect(screen.getByText("How do you get paid today?")).toBeInTheDocument()
    expect(screen.getByRole("button", { pressed: true })).toHaveTextContent(
      "I’m already on insurance panels",
    )
  })
})

describe("the steps she is shown", () => {
  it("shows only the shared spine before she answers", () => {
    // A stepper that grew three entries the moment she clicked would make the
    // choice feel like it cost her something.
    render(<GetPaidWizard />)

    expect(screen.getByText("Practice details")).toBeInTheDocument()
    expect(screen.getByText("Your rates")).toBeInTheDocument()
    expect(screen.queryByText("Payers")).not.toBeInTheDocument()
  })

  it("adds no insurance steps for a private-pay practice", () => {
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByText("My clients pay me directly"))

    expect(screen.queryByText("Payers")).not.toBeInTheDocument()
    expect(screen.queryByText("What we found")).not.toBeInTheDocument()
  })

  it("adds the payer step, and no record steps, for someone already paneled", () => {
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByText("I’m already on insurance panels"))

    expect(screen.getByText("Payers")).toBeInTheDocument()
    // She is paneled. Nothing should ask her to confirm an NPI she has held
    // for a decade, or walk her through credentialing she has already done.
    expect(screen.queryByText("What we found")).not.toBeInTheDocument()
  })

  it("adds the record steps for someone who wants a panel", () => {
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByText("I want to accept insurance, but I’m not on a panel yet"))

    expect(screen.getByText("Payers")).toBeInTheDocument()
    expect(screen.getByText("What we found")).toBeInTheDocument()
    expect(screen.getByText("Your record")).toBeInTheDocument()
  })
})
