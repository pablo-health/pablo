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

  it("links the help articles beside the support address", async () => {
    renderWithProviders(<HelpNavItem />, {
      config: testRuntimeConfig({
        supportEmail: "help@clinic.example",
        helpUrl: "https://clinic.example/help",
      }),
    })

    fireEvent.click(await screen.findByRole("button", { name: "Help" }))

    const articles = await screen.findByRole("link", { name: "Help articles" })
    expect(articles).toHaveAttribute("href", "https://clinic.example/help")
    expect(articles).toHaveAttribute("target", "_blank")
    expect(screen.getByRole("link", { name: "help@clinic.example" })).toBeInTheDocument()
  })

  it("shows Help with only a help-articles page configured", async () => {
    renderWithProviders(<HelpNavItem />, {
      config: testRuntimeConfig({ supportEmail: "", helpUrl: "https://clinic.example/help" }),
    })

    fireEvent.click(await screen.findByRole("button", { name: "Help" }))

    expect(await screen.findByRole("link", { name: "Help articles" })).toBeInTheDocument()
    expect(screen.queryByText(/^Email/)).not.toBeInTheDocument()
  })

  it("never links a help URL that isn't http(s)", async () => {
    renderWithProviders(<HelpNavItem />, {
      config: testRuntimeConfig({
        supportEmail: "help@clinic.example",
        helpUrl: "javascript:alert(1)",
      }),
    })

    fireEvent.click(await screen.findByRole("button", { name: "Help" }))

    await screen.findByRole("link", { name: "help@clinic.example" })
    expect(screen.queryByRole("link", { name: "Help articles" })).not.toBeInTheDocument()
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
