// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The portal's front door, for anyone without a session: sign in by email,
 * then "check your email" for every address once the request got through,
 * and back to the form for a different address. A visitor on an expired
 * sign-in or link lands on the same form with one line saying so.
 *
 * What the page says after a submission is pinned word for word in
 * recover.test.tsx; this is the landing's side of the same flow.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { PortalShell } from "../PortalShell"

const resolvePortalPractice = vi.fn()
const requestPortalRecovery = vi.fn()
const bootstrapSession = vi.fn()

vi.mock("@/lib/portal-shell/api", () => ({
  resolvePortalPractice: (...args: unknown[]) => resolvePortalPractice(...args),
  requestPortalRecovery: (...args: unknown[]) => requestPortalRecovery(...args),
  requestSignInCode: vi.fn(),
  fetchCapabilities: vi.fn(),
}))

vi.mock("@/lib/portal-shell/session", () => ({
  bootstrapSession: (...args: unknown[]) => bootstrapSession(...args),
  redeemAndStore: vi.fn(),
  signOutAndForget: vi.fn(),
}))

vi.mock("next/navigation", () => ({ usePathname: () => "/portal/example-therapy" }))

const SLUG = "example-therapy"
const SENT = "If we find a portal account for this email, we'll send a new sign-in link."

beforeEach(() => {
  vi.clearAllMocks()
  resolvePortalPractice.mockResolvedValue({
    ok: true,
    data: { slug: SLUG, display_name: "Example Therapy", captcha_site_key: null },
  })
  bootstrapSession.mockResolvedValue({ status: "none" })
  requestPortalRecovery.mockResolvedValue({ ok: true })
})

async function submit(email: string): Promise<void> {
  await userEvent.type(await screen.findByLabelText("Email"), email)
  await userEvent.click(screen.getByRole("button", { name: "Email me a sign-in link" }))
}

describe("signing in from the portal's landing", () => {
  it("opens on sign-in, not on 'check your email'", async () => {
    render(<PortalShell slug={SLUG} />)

    const card = await screen.findByTestId("portal-shell-no-session")
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeTruthy()
    expect(card).toHaveTextContent("Enter your email and we'll send you a link to sign in.")
    expect(card).toHaveTextContent("New here? Your practice will send you an invitation.")
    expect(screen.getByRole("button", { name: "Email me a sign-in link" })).toBeTruthy()
    expect(screen.queryByText("Check your email")).toBeNull()
    expect(screen.queryByTestId("portal-shell-expired-note")).toBeNull()
  })

  it("says to check email once the request got through, for every address", async () => {
    render(<PortalShell slug={SLUG} />)

    await submit("ada@example.test")

    expect(requestPortalRecovery).toHaveBeenCalledWith(SLUG, "ada@example.test", null)
    const heading = await screen.findByRole("heading", { name: "Check your email" })
    expect(document.activeElement).toBe(heading)
    expect(screen.getByRole("status")).toHaveTextContent(SENT)
    expect(screen.queryByLabelText("Email")).toBeNull()
  })

  it("goes back to the form for a different address", async () => {
    render(<PortalShell slug={SLUG} />)
    await submit("ada@example.test")

    await userEvent.click(await screen.findByRole("button", { name: "Use a different email" }))

    expect(screen.getByLabelText("Email")).toHaveValue("")
    expect(screen.queryByText("Check your email")).toBeNull()
  })

  it("keeps the form and says to try again when the request did not get through", async () => {
    requestPortalRecovery.mockResolvedValue({ ok: false })
    render(<PortalShell slug={SLUG} />)

    await submit("ada@example.test")

    expect(await screen.findByTestId("portal-recover-failed")).toHaveTextContent("Wait a minute and try again.")
    expect(screen.getByLabelText("Email")).toBeTruthy()
    expect(screen.queryByText("Check your email")).toBeNull()
  })

  it("lands an expired sign-in on the same form, with one line saying so", async () => {
    bootstrapSession.mockResolvedValue({ status: "expired" })
    render(<PortalShell slug={SLUG} />)

    expect(await screen.findByTestId("portal-shell-expired-note")).toHaveTextContent("Your sign-in has expired.")
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "Email me a sign-in link" })).toBeTruthy()
  })

  it("greets the visitor above the card", async () => {
    render(<PortalShell slug={SLUG} />)

    expect(await screen.findByRole("heading", { name: "Welcome to Example Therapy" })).toBeTruthy()
  })
})
