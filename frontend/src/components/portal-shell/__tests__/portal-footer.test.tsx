// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * On the practice's own host the portal is part of the practice's site: no
 * "Powered by Pablo", the practice's name and a crisis line in the footer,
 * and a link back to its website when it has a live one. Everywhere else the
 * portal keeps its own footer. Decided by the host, not the theme: a practice
 * on its own domain with no theme.json is still on its own domain.
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
const CRISIS = "In crisis? Call or text 988, or call 911."

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
  it("keeps its own footer away from the practice's own host, and links to no website", async () => {
    render(page())

    await screen.findByTestId("portal-shell-practice-name")
    expect(screen.getByTestId("portal-footer")).toHaveTextContent("Powered by Pablo")
    expect(screen.getByTestId("portal-footer")).not.toHaveTextContent(CRISIS)
    expect(screen.queryByTestId("portal-back-to-site")).not.toBeInTheDocument()
  })

  it("on the practice's own host, names the practice and a crisis line instead", async () => {
    render(<PortalHostProvider value={{ onPracticeHost: true, siteHost: null, header: null }}>{page()}</PortalHostProvider>)

    await screen.findByTestId("portal-shell-practice-name")
    const footer = screen.getByTestId("portal-footer")
    expect(footer).toHaveTextContent("Example Therapy")
    expect(footer).toHaveTextContent(CRISIS)
    expect(screen.queryByText("Powered by Pablo")).not.toBeInTheDocument()
    // No live website, no link back to one.
    expect(screen.queryByTestId("portal-back-to-site")).not.toBeInTheDocument()
  })

  it("links back to the practice's live website", async () => {
    render(
      <PortalHostProvider value={{ onPracticeHost: true, siteHost: "www.example-therapy.com", header: null }}>
        {page()}
      </PortalHostProvider>,
    )

    const back = await screen.findByTestId("portal-back-to-site")
    expect(back).toHaveTextContent("Back to www.example-therapy.com")
    expect(back).toHaveAttribute("href", "https://www.example-therapy.com")
  })

  it("greets the visitor with the practice's name, in the heading font", async () => {
    render(page())

    const welcome = await screen.findByTestId("portal-welcome")
    expect(await screen.findByRole("heading", { name: "Welcome to Example Therapy" })).toHaveClass("font-display")
    expect(welcome).toHaveTextContent("This is your client portal.")
    expect(screen.getByTestId("portal-shell-practice-name")).toHaveClass("font-display")
  })
})
