// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { fireEvent, screen, waitFor } from "@testing-library/react"
import { renderWithProviders } from "@/test/renderWithProviders"
import { testRuntimeConfig } from "@/test/runtimeConfig"
import { HelpNavItem } from "../HelpNavItem"

describe("HelpNavItem", () => {
  it("opens to the configured support address", async () => {
    renderWithProviders(<HelpNavItem />, {
      config: testRuntimeConfig({ supportEmail: "help@clinic.example" }),
    })

    fireEvent.click(await screen.findByRole("button", { name: "Help" }))

    const link = await screen.findByRole("link", { name: "help@clinic.example" })
    expect(link).toHaveAttribute("href", "mailto:help@clinic.example")
  })

  it("is absent when the deployment configured no address", async () => {
    renderWithProviders(
      <>
        <span>loaded</span>
        <HelpNavItem />
      </>,
      { config: testRuntimeConfig({ supportEmail: "" }) },
    )

    await screen.findByText("loaded")
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Help" })).not.toBeInTheDocument()
    })
  })
})
