// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, fireEvent, waitFor } from "@testing-library/react"
import { renderWithProviders } from "@/test/renderWithProviders"
import { ImportSourceStep, IMPORT_SCREEN_PATH } from "../ImportSourceStep"

const push = vi.fn()
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }))

const updateUserProfile = vi.fn().mockResolvedValue({})
vi.mock("@/lib/api/users", () => ({
  updateUserProfile: (...args: unknown[]) => updateUserProfile(...args),
}))

const trackOnboardingStepSkipped = vi.fn()
vi.mock("@/lib/analytics/onboarding", () => ({
  trackOnboardingStepSkipped: (...args: unknown[]) => trackOnboardingStepSkipped(...args),
}))

describe("ImportSourceStep", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    updateUserProfile.mockResolvedValue({})
  })

  it("offers SimplePractice and another system, with SimplePractice chosen first", () => {
    renderWithProviders(<ImportSourceStep />)
    const sp = screen.getByRole("radio", { name: /SimplePractice/ })
    const other = screen.getByRole("radio", { name: /Another system/ })
    expect(sp).toHaveAttribute("aria-checked", "true")
    expect(other).toHaveAttribute("aria-checked", "false")
  })

  it("records SimplePractice and opens the import screen", async () => {
    renderWithProviders(<ImportSourceStep />)
    fireEvent.click(screen.getByText("Continue"))
    await waitFor(() => expect(push).toHaveBeenCalledWith(IMPORT_SCREEN_PATH))
    expect(updateUserProfile).toHaveBeenCalledWith({ import_source: "simplepractice" })
  })

  it("records another system and carries on with setup", async () => {
    renderWithProviders(<ImportSourceStep />)
    fireEvent.click(screen.getByRole("radio", { name: /Another system/ }))
    fireEvent.click(screen.getByText("Continue"))
    await waitFor(() => expect(push).toHaveBeenCalledWith("/onboarding"))
    expect(updateUserProfile).toHaveBeenCalledWith({ import_source: "other" })
  })

  it("Skip records only that it was asked", async () => {
    renderWithProviders(<ImportSourceStep />)
    fireEvent.click(screen.getByText("Skip"))
    await waitFor(() => expect(push).toHaveBeenCalledWith("/onboarding"))
    expect(updateUserProfile).toHaveBeenCalledWith({ import_prompted: true })
    expect(trackOnboardingStepSkipped).toHaveBeenCalledWith("import-source")
  })

  it("stays on the step and says so when saving fails", async () => {
    updateUserProfile.mockRejectedValueOnce(new Error("offline"))
    renderWithProviders(<ImportSourceStep />)
    fireEvent.click(screen.getByText("Continue"))
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong")
    expect(push).not.toHaveBeenCalled()
  })
})
