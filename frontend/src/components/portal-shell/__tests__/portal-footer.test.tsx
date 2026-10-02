// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * On the practice's own host the portal is part of the practice's site and
 * carries no "Powered by Pablo"; everywhere else it does. Decided by the host,
 * not the theme: a practice on its own domain with no theme.json drops it too.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { PortalRecover } from "../PortalRecover"
import { PortalShell } from "../PortalShell"
import { PortalHostProvider } from "../portal-host-context"

const resolvePortalPractice = vi.fn()

vi.mock("@/lib/portal-shell/api", () => ({
  resolvePortalPractice: (...args: unknown[]) => resolvePortalPractice(...args),
  requestPortalRecovery: vi.fn(),
  requestSignInCode: vi.fn(),
  fetchCapabilities: vi.fn(),
}))

vi.mock("@/lib/portal-shell/session", () => ({
  bootstrapSession: vi.fn(async () => ({ status: "none" })),
  redeemAndStore: vi.fn(),
  signOutAndForget: vi.fn(),
}))

vi.mock("next/navigation", () => ({ usePathname: () => "/portal/example-therapy" }))

const SLUG = "example-therapy"

beforeEach(() => {
  resolvePortalPractice.mockResolvedValue({
    ok: true,
    data: { slug: SLUG, display_name: "Example Therapy", captcha_site_key: null },
  })
})

describe.each([
  ["the portal shell", () => <PortalShell slug={SLUG} />],
  ["the recovery page", () => <PortalRecover slug={SLUG} />],
])("%s", (_name, page) => {
  it("says what it runs on away from the practice's own host", async () => {
    render(page())

    await screen.findByText("Example Therapy")
    expect(screen.getByTestId("portal-footer")).toHaveTextContent("Powered by Pablo")
  })

  it("says nothing of it on the practice's own host", async () => {
    render(<PortalHostProvider value={{ onPracticeHost: true }}>{page()}</PortalHostProvider>)

    await screen.findByText("Example Therapy")
    expect(screen.queryByText("Powered by Pablo")).not.toBeInTheDocument()
  })
})
