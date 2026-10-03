// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Before the portal or the practice's theme exists, the app waits on its
 * runtime config. A portal visitor waits on a quiet, neutral screen that says
 * only "Loading…"; the clinician app keeps the screen it always had.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { isPortalPage } from "@/components/providers"
import { ConfigProvider } from "@/lib/config-provider"
import { PortalBootLoading } from "../PortalBootLoading"

function renderWaiting(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

beforeEach(() => {
  // The config request never answers, so the loading screen stays up.
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("waiting on the runtime config", () => {
  it("shows a portal visitor a neutral screen with no technical words", () => {
    renderWaiting(
      <ConfigProvider loading={<PortalBootLoading />}>
        <p>portal</p>
      </ConfigProvider>,
    )

    const loading = screen.getByTestId("portal-boot-loading")
    expect(loading).toHaveTextContent(/^Loading…$/)
    expect(loading).toHaveAttribute("role", "status")
    expect(loading.innerHTML).not.toMatch(/blue/)
    expect(screen.queryByText(/configuration/i)).toBeNull()
    expect(screen.queryByText("portal")).toBeNull()
  })

  it("keeps the clinician app's own screen when no portal screen is given", () => {
    renderWaiting(
      <ConfigProvider>
        <p>app</p>
      </ConfigProvider>,
    )

    expect(screen.getByText("Loading configuration...")).toBeInTheDocument()
    expect(screen.queryByTestId("portal-boot-loading")).toBeNull()
  })
})

describe("isPortalPage", () => {
  it.each([
    ["/portal/example-therapy", false],
    ["/portal", false],
    ["/", true],
    ["/example-therapy/forms", true],
  ])("treats %s as the portal (portal-only host: %s)", (pathname, portalOnlyHost) => {
    expect(isPortalPage(pathname, portalOnlyHost)).toBe(true)
  })

  it.each(["/", "/dashboard", "/portals", "/login", null])("leaves %s on this deployment's own host to the app", (pathname) => {
    expect(isPortalPage(pathname, false)).toBe(false)
  })
})
