// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import { useQuery } from "@tanstack/react-query"
import { useConfig, type RuntimeConfig } from "@/lib/config-provider"
import { renderWithProviders } from "../renderWithProviders"

function QueryProbe() {
  const { data } = useQuery({
    queryKey: ["probe"],
    queryFn: async () => "ok",
  })
  return <div>query:{data ?? "loading"}</div>
}

function ConfigProbe() {
  const config = useConfig()
  return <div>apiUrl:{config.apiUrl}</div>
}

const TEST_CONFIG: RuntimeConfig = {
  apiUrl: "https://api.test",
  devMode: false,
  dataMode: "live",
  enableLocalAuth: false,
  firebaseProjectId: "test-project",
  firebaseApiKey: "test-key",
  firebaseAuthDomain: "test.firebaseapp.com",
  firebaseAppId: "test-app-id",
  ratingFeedbackRequiredBelow: 0,
  showVerificationBadges: false,
  introVideoUrl: "",
  passkeysEnabled: false,
  resubscribeUrl: "",
  publicBookingEnabled: false,
}

describe("renderWithProviders", () => {
  it("renders a useQuery consumer without throwing", async () => {
    renderWithProviders(<QueryProbe />)

    expect(await screen.findByText("query:ok")).toBeInTheDocument()
  })

  it("throws under plain render() because there is no QueryClient", () => {
    expect(() => render(<QueryProbe />)).toThrow(/No QueryClient set/)
  })

  it("renders a useConfig consumer when given a config", async () => {
    renderWithProviders(<ConfigProbe />, { config: TEST_CONFIG })

    expect(await screen.findByText("apiUrl:https://api.test")).toBeInTheDocument()
  })

  it("throws under plain render() because there is no ConfigProvider", () => {
    expect(() => render(<ConfigProbe />)).toThrow(
      /useConfig must be used within ConfigProvider/,
    )
  })
})
