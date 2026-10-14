// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { renderWithProviders } from "@/test/renderWithProviders"
import { testRuntimeConfig } from "@/test/runtimeConfig"
import { PanelLoadError } from "../PanelLoadError"

describe("PanelLoadError", () => {
  it("offers the configured support address beside the retry", async () => {
    const onRetry = vi.fn()
    renderWithProviders(
      <PanelLoadError message="Today’s sessions didn’t load." onRetry={onRetry} />,
      { config: testRuntimeConfig({ supportEmail: "help@clinic.example" }) },
    )

    const link = await screen.findByRole("link", { name: "help@clinic.example" })
    expect(link).toHaveAttribute("href", "mailto:help@clinic.example")
    expect(screen.getByTestId("support-contact-line")).toHaveTextContent(
      "Still not loading? Email help@clinic.example.",
    )

    fireEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it("shows the failure and the retry but no contact line when no address is configured", async () => {
    renderWithProviders(
      <PanelLoadError message="Today’s sessions didn’t load." onRetry={() => {}} />,
      { config: testRuntimeConfig({ supportEmail: "" }) },
    )

    expect(await screen.findByText("Today’s sessions didn’t load.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument()
    expect(screen.queryByTestId("support-contact-line")).not.toBeInTheDocument()
    expect(screen.queryByRole("link")).not.toBeInTheDocument()
  })

  it("renders without a ConfigProvider, minus the contact line", () => {
    render(<PanelLoadError message="Your reminders didn’t load." onRetry={() => {}} />)

    expect(screen.getByRole("alert")).toHaveTextContent("Your reminders didn’t load.")
    expect(screen.queryByTestId("support-contact-line")).not.toBeInTheDocument()
  })
})
