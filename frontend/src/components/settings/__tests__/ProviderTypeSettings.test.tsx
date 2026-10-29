// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The Profile page renders this control once preferences load, and the saved
 * clinician type arrives separately with user status. When status landed
 * second, the control had already copied `null` into state and showed an
 * empty select over a saved type.
 */

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ProviderTypeSettings } from "../ProviderTypeSettings"
import type { ProviderType } from "@/lib/api/users"

vi.mock("@/lib/api/users", () => ({
  updateUserProfile: vi.fn(),
}))

function renderControl(currentValue: ProviderType | null) {
  const queryClient = new QueryClient()
  const ui = (value: ProviderType | null) => (
    <QueryClientProvider client={queryClient}>
      <ProviderTypeSettings currentValue={value} />
    </QueryClientProvider>
  )
  const view = render(ui(currentValue))
  return { rerender: (value: ProviderType | null) => view.rerender(ui(value)) }
}

describe("ProviderTypeSettings", () => {
  it("shows the saved type", () => {
    renderControl("both")
    expect(screen.getByText("Provides therapy and manages medications.")).toBeInTheDocument()
  })

  it("shows the saved type when it arrives after the first render", () => {
    const { rerender } = renderControl(null)
    expect(screen.queryByText("Provides therapy and manages medications.")).toBeNull()

    rerender("both")

    expect(screen.getByText("Provides therapy and manages medications.")).toBeInTheDocument()
  })
})
