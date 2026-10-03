// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The shell's header on the practice's own host when the website's
 * theme.json declares a header: the wordmark and subtitle linking to the
 * website, its links in a row of their own before the portal's sections, its
 * call to action, and the practice's record name still the heading and the
 * header's name. Signed in or out, the same header.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, within } from "@testing-library/react"
import type { PracticeHeader } from "@/lib/portal-host/practice-header"
import { PortalShell } from "../PortalShell"
import { ShellHeader } from "../PortalNav"
import { PortalHostProvider } from "../portal-host-context"
import type { PortalSlot } from "../slots"

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

vi.mock("next/navigation", () => ({ usePathname: () => "/portal/riverside" }))

const SITE = "www.riverside.example"
const HEADER: PracticeHeader = {
  wordmark: "Riverside Counseling",
  subtitle: "Individual and couples therapy",
  links: [
    { label: "Services", href: `https://${SITE}/#services` },
    { label: "About", href: `https://${SITE}/about` },
  ],
  cta: { label: "Schedule a visit", href: `https://${SITE}/#schedule` },
}
const SLOTS: PortalSlot[] = [{ id: "messaging", label: "Messages", Component: () => null }]

function onPracticeHost(children: React.ReactNode, header: PracticeHeader | null = HEADER) {
  return (
    <PortalHostProvider value={{ onPracticeHost: true, siteHost: SITE, header }}>{children}</PortalHostProvider>
  )
}

function signedIn(displayName: string, header: PracticeHeader | null = HEADER) {
  return onPracticeHost(
    <ShellHeader
      displayName={displayName}
      slots={SLOTS}
      base="/"
      section={null}
      onSignOut={() => undefined}
      signingOut={false}
    />,
    header,
  )
}

beforeEach(() => {
  resolvePortalPractice.mockResolvedValue({
    ok: true,
    data: { slug: "riverside", display_name: "Riverside Counseling, PLLC", captcha_site_key: null },
  })
})

describe("the website's header in the portal", () => {
  it("shows the wordmark, subtitle, links and call to action to a signed-out visitor", async () => {
    render(onPracticeHost(<PortalShell slug="riverside" />))

    const banner = await screen.findByRole("banner", { name: "Riverside Counseling, PLLC" })
    expect(screen.getByRole("heading", { level: 1, name: "Riverside Counseling, PLLC" })).toHaveAttribute(
      "data-testid",
      "portal-shell-practice-name",
    )
    const wordmark = within(banner).getByTestId("portal-header-wordmark")
    expect(wordmark).toHaveTextContent("Riverside Counseling")
    expect(wordmark).toHaveAttribute("href", `https://${SITE}/`)
    expect(within(banner).getByTestId("portal-header-subtitle")).toHaveTextContent("Individual and couples therapy")
    const links = within(banner).getByRole("navigation", { name: "Practice website" })
    expect(within(links).getAllByRole("link").map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      ["Services", `https://${SITE}/#services`],
      ["About", `https://${SITE}/about`],
    ])
    for (const link of within(links).getAllByRole("link")) {
      expect(link).toHaveAttribute("rel", "noopener")
      expect(link).not.toHaveAttribute("target")
    }
    expect(within(banner).getByRole("link", { name: "Schedule a visit" })).toHaveAttribute(
      "href",
      `https://${SITE}/#schedule`,
    )
    // The wordmark is the way back to the website now.
    expect(screen.queryByTestId("portal-back-to-site")).not.toBeInTheDocument()
  })

  it("shows the same header signed in, before the portal's own sections", () => {
    render(signedIn("Riverside Counseling, PLLC"))

    const banner = screen.getByRole("banner", { name: "Riverside Counseling, PLLC" })
    const navs = within(banner).getAllByRole("navigation")
    expect(navs.map((nav) => nav.getAttribute("aria-label"))).toEqual(["Practice website", "Portal sections"])
    expect(within(banner).getByTestId("portal-header-wordmark")).toHaveTextContent("Riverside Counseling")
    expect(within(banner).getByRole("link", { name: "Schedule a visit" })).toBeVisible()
    expect(within(banner).getByRole("button", { name: "Sign out" })).toBeVisible()
  })

  it("shows the name once when the wordmark is the practice's name", () => {
    render(signedIn("riverside  counseling"))

    const heading = screen.getByRole("heading", { level: 1 })
    expect(heading).toHaveTextContent("riverside counseling")
    expect(heading).not.toHaveClass("sr-only")
    expect(within(heading).getByRole("link")).toHaveAttribute("href", `https://${SITE}/`)
    expect(screen.queryByText("Riverside Counseling")).not.toBeInTheDocument()
  })

  it("renders the values as text, never as markup", () => {
    render(signedIn("Riverside", { ...HEADER, wordmark: "<b>Bold</b> & <script>x</script>" }))

    expect(screen.getByTestId("portal-header-wordmark")).toHaveTextContent("<b>Bold</b> & <script>x</script>")
    expect(document.querySelector("header b, header script")).toBeNull()
  })

  it("keeps the plain link back without a header", () => {
    render(signedIn("Riverside", null))

    expect(screen.getByTestId("portal-back-to-site")).toHaveAttribute("href", `https://${SITE}`)
    expect(screen.queryByRole("navigation", { name: "Practice website" })).not.toBeInTheDocument()
    expect(screen.getByRole("banner")).not.toHaveAttribute("aria-label")
  })
})
