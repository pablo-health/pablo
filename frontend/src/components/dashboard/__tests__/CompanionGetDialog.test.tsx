// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import { renderWithProviders } from "@/test/renderWithProviders"
import { CompanionGetDialog } from "../CompanionGetDialog"

const isMacOS = vi.hoisted(() => vi.fn())
const useCompanionDownloadUrl = vi.hoisted(() => vi.fn())
const needsServerAddress = vi.hoisted(() => vi.fn((_origin: string) => false))

vi.mock("@/lib/companion", () => ({
  companionPlatform: () => {
    const mac = isMacOS() as boolean | "windows"
    if (mac === "windows") return "windows"
    return mac ? "macos" : null
  },
  companionNeedsServerAddress: (origin: string) => needsServerAddress(origin),
}))

vi.mock("@/lib/companion.extensions", () => ({
  useCompanionAccess: () => true,
  useCompanionDownloadUrl: (...args: unknown[]) =>
    useCompanionDownloadUrl(...args),
}))

afterEach(() => {
  vi.clearAllMocks()
  needsServerAddress.mockReturnValue(false)
})

describe("CompanionGetDialog", () => {
  it("links the platform's download on macOS", () => {
    isMacOS.mockReturnValue(true)
    useCompanionDownloadUrl.mockReturnValue("https://downloads.example/Pablo.dmg")

    renderWithProviders(
      <CompanionGetDialog open={true} onOpenChange={() => {}} />,
    )

    expect(useCompanionDownloadUrl).toHaveBeenCalledWith("macos")
    const link = screen.getByRole("link", { name: "Download for macOS" })
    expect(link).toHaveAttribute("href", "https://downloads.example/Pablo.dmg")
    expect(link).toHaveAttribute("target", "_blank")
    // Installing is not enough: the dashboard only sees the app once the
    // clinician signs in from it.
    expect(
      screen.getByText("After installing, open the app and sign in."),
    ).toBeInTheDocument()
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
    useCompanionDownloadUrl.mockReturnValue(null)

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

  it("names Windows and links the download on Windows", () => {
    isMacOS.mockReturnValue("windows")
    useCompanionDownloadUrl.mockReturnValue("https://downloads.example/win")

    renderWithProviders(
      <CompanionGetDialog open={true} onOpenChange={() => {}} />,
    )

    expect(screen.getByText("Get Pablo for Windows")).toBeInTheDocument()
    expect(useCompanionDownloadUrl).toHaveBeenCalledWith("windows")
    const link = screen.getByRole("link", { name: "Download for Windows" })
    expect(link).toHaveAttribute("href", "https://downloads.example/win")
  })

  it("gives the address to enter in the app on a deployment elsewhere", () => {
    isMacOS.mockReturnValue(true)
    useCompanionDownloadUrl.mockReturnValue("https://downloads.example/Pablo.dmg")
    needsServerAddress.mockReturnValue(true)

    renderWithProviders(
      <CompanionGetDialog open={true} onOpenChange={() => {}} />,
    )

    expect(needsServerAddress).toHaveBeenCalledWith(window.location.origin)
    expect(screen.getByText(/choose Connect to a different server/)).toBeInTheDocument()
    expect(screen.getByText(window.location.origin)).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Copy this Pablo's address" }),
    ).toBeInTheDocument()
    expect(
      screen.queryByText("After installing, open the app and sign in."),
    ).not.toBeInTheDocument()
  })

  it("asks for no address on the desktop app's default server", () => {
    isMacOS.mockReturnValue("windows")
    useCompanionDownloadUrl.mockReturnValue("https://downloads.example/win")
    needsServerAddress.mockReturnValue(false)

    renderWithProviders(
      <CompanionGetDialog open={true} onOpenChange={() => {}} />,
    )

    expect(screen.queryByText(/Connect to a different server/)).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Copy this Pablo's address" }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByText("After installing, open the app and sign in."),
    ).toBeInTheDocument()
  })
})
