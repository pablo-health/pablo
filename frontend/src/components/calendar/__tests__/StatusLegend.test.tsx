// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { StatusLegend } from "../StatusLegend"

describe("StatusLegend", () => {
  it("renders all four appointment statuses", () => {
    render(<StatusLegend />)

    expect(screen.getByText("Confirmed")).toBeInTheDocument()
    expect(screen.getByText("Completed")).toBeInTheDocument()
    expect(screen.getByText("Cancelled")).toBeInTheDocument()
    expect(screen.getByText("No Show")).toBeInTheDocument()
  })

  it("has accessible list role with label", () => {
    render(<StatusLegend />)

    const list = screen.getByRole("list", { name: /appointment status legend/i })
    expect(list).toBeInTheDocument()
  })

  it("renders each status as a list item", () => {
    render(<StatusLegend />)

    const items = screen.getAllByRole("listitem")
    expect(items).toHaveLength(4)
  })
})
