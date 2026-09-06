// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { render } from "@testing-library/react"
import { BillingSetupSlot } from "../BillingSetupSlot"

describe("BillingSetupSlot", () => {
  it("renders nothing by default", () => {
    const { container } = render(<BillingSetupSlot />)
    expect(container).toBeEmptyDOMElement()
  })
})
