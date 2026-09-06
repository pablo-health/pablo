// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import { BillingSetupGate } from "../BillingSetupGate"

describe("BillingSetupGate", () => {
  it("renders its children unchanged, adding nothing of its own", () => {
    const { container } = render(
      <BillingSetupGate>
        <p>unbilled queue</p>
      </BillingSetupGate>
    )

    expect(screen.getByText("unbilled queue")).toBeInTheDocument()
    // The base build must not wrap children in markup of its own — a
    // replacement is free to, but anything added here would show up in every
    // downstream build's Billing page.
    expect(container.innerHTML).toBe("<p>unbilled queue</p>")
  })
})
