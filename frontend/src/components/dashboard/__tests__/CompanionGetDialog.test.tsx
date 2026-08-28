// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import { renderWithProviders } from "@/test/renderWithProviders"
import { CompanionGetDialog } from "../CompanionGetDialog"

const isMacOS = vi.hoisted(() => vi.fn())
const useCompanionDownloadUrl = vi.hoisted(() => vi.fn())

vi.mock("@/lib/companion", () => ({
  isMacOS: (...args: unknown[]) => isMacOS(...args),
}))

vi.mock("@/lib/companion.extensions", () => ({
  useCompanionAccess: () => true,
  useCompanionDownloadUrl: (...args: unknown[]) =>
    useCompanionDownloadUrl(...args),
}))

afterEach(() => {
  vi.clearAllMocks()
})

describe("CompanionGetDialog", () => {
  it("links to the default download URL on macOS", () => {
    isMacOS.mockReturnValue(true)
    useCompanionDownloadUrl.mockReturnValue("https://pablo.health")

    renderWithProviders(
      <CompanionGetDialog open={true} onOpenChange={() => {}} />,
    )

    const link = screen.getByRole("link", { name: "Download for macOS" })
    expect(link).toHaveAttribute("href", "https://pablo.health")
    expect(link).toHaveAttribute("target", "_blank")
  })

  it("links to a deployment-provided download URL", () => {
    isMacOS.mockReturnValue(true)
    useCompanionDownloadUrl.mockReturnValue("https://downloads.example/mac.dmg")

    renderWithProviders(
      <CompanionGetDialog open={true} onOpenChange={() => {}} />,
    )

    const link = screen.getByRole("link", { name: "Download for macOS" })
    expect(link).toHaveAttribute("href", "https://downloads.example/mac.dmg")
  })

  it("renders a disabled button with no link when there is no artifact", () => {
    isMacOS.mockReturnValue(true)
    useCompanionDownloadUrl.mockReturnValue(null)

    renderWithProviders(
      <CompanionGetDialog open={true} onOpenChange={() => {}} />,
    )

    expect(
      screen.queryByRole("link", { name: "Download for macOS" }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Download for macOS" }),
    ).toBeDisabled()
    expect(screen.getByText(/Download unavailable/)).toBeInTheDocument()
  })

  it("shows unsupported-platform copy and no link on non-macOS", () => {
    isMacOS.mockReturnValue(false)
    useCompanionDownloadUrl.mockReturnValue("https://pablo.health")

    renderWithProviders(
      <CompanionGetDialog open={true} onOpenChange={() => {}} />,
    )

    expect(
      screen.queryByRole("link", { name: "Download for macOS" }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByText(/isn't supported yet/),
    ).toBeInTheDocument()
  })
})
