// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { OperationalPanels } from "../OperationalPanels"

const readOnlyMode = { readOnly: false }
vi.mock("@/lib/access/readOnlyMode", () => ({
  useReadOnlyMode: () => readOnlyMode,
}))

beforeEach(() => {
  readOnlyMode.readOnly = false
})

describe("OperationalPanels", () => {
  it("renders its children when writable", () => {
    render(
      <OperationalPanels>
        <div>today panel</div>
      </OperationalPanels>,
    )
    expect(screen.getByText("today panel")).toBeInTheDocument()
  })

  it("renders nothing in read-only mode", () => {
    readOnlyMode.readOnly = true
    render(
      <OperationalPanels>
        <div>today panel</div>
      </OperationalPanels>,
    )
    expect(screen.queryByText("today panel")).not.toBeInTheDocument()
  })
})
