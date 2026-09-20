// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PortalShell — every state the shell can be in.
 *
 * Pins the state machine driven entirely off the mocked
 * `@/lib/portal-shell/{api,session}` fetchers: resolving, unknown practice,
 * no-session, code entry (when `?invite=` is present), the active shell's
 * zero-slot empty card, and the expired/revoked note. A mounted slot is
 * handed the slug and the LIVE session token, including the one a redeem
 * just minted.
 *
 * Also pins that every redeem failure — whatever the cause — renders the
 * same generic error, since the backend's uniform 401 is the whole point of
 * that surface.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { PortalShell } from "../PortalShell"
import { resetPortalSlotsForTests, registerPortalSlot, type PortalSlotProps } from "../slots"

const resolvePortalPractice = vi.fn()
const bootstrapSession = vi.fn()
const redeemAndStore = vi.fn()

vi.mock("@/lib/portal-shell/api", () => ({
  resolvePortalPractice: (...args: unknown[]) => resolvePortalPractice(...args),
}))

vi.mock("@/lib/portal-shell/session", () => ({
  bootstrapSession: (...args: unknown[]) => bootstrapSession(...args),
  redeemAndStore: (...args: unknown[]) => redeemAndStore(...args),
}))

let searchParams = new URLSearchParams()
vi.mock("next/navigation", () => ({
  useSearchParams: () => searchParams,
}))

beforeEach(() => {
  vi.clearAllMocks()
  searchParams = new URLSearchParams()
  // The shell registers the engine's own modules when it is imported. A
  // spec about the shell's states drives its own slots instead.
  resetPortalSlotsForTests()
})

describe("PortalShell", () => {
  it("renders a skeleton while resolving", () => {
    resolvePortalPractice.mockReturnValue(new Promise(() => {}))

    render(<PortalShell slug="example-therapy" />)

    expect(screen.getByTestId("portal-shell-skeleton")).toBeTruthy()
  })

  it("renders the generic unknown-practice state for an unresolvable slug", async () => {
    resolvePortalPractice.mockResolvedValue({ ok: false })

    render(<PortalShell slug="never-existed" />)

    expect(await screen.findByTestId("portal-shell-unknown")).toBeTruthy()
  })

  it("renders the display name once the slug resolves", async () => {
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })

    render(<PortalShell slug="example-therapy" />)

    expect(await screen.findByText("Example Therapy")).toBeTruthy()
  })

  it("renders the no-session empty state with no stored session and no invite", async () => {
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })

    render(<PortalShell slug="example-therapy" />)

    expect(await screen.findByTestId("portal-shell-no-session")).toBeTruthy()
    expect(screen.queryByTestId("portal-shell-expired-note")).toBeNull()
  })

  it("renders the code form when ?invite= is present and there is no session", async () => {
    searchParams = new URLSearchParams({ invite: "tok-1" })
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })

    render(<PortalShell slug="example-therapy" />)

    expect(await screen.findByTestId("portal-shell-otp")).toBeTruthy()
  })

  it("renders the active shell's empty card when no slots are registered", async () => {
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "active", sessionToken: "session-1" })

    render(<PortalShell slug="example-therapy" />)

    expect(await screen.findByTestId("portal-shell-active")).toBeTruthy()
    expect(screen.getByTestId("portal-shell-empty")).toBeTruthy()
  })

  it("renders a registered slot inside the active shell", async () => {
    registerPortalSlot({ id: "fake-slot", Component: () => <div>Fake slot content</div> })
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "active", sessionToken: "session-1" })

    render(<PortalShell slug="example-therapy" />)

    expect(await screen.findByText("Fake slot content")).toBeTruthy()
    expect(screen.queryByTestId("portal-shell-empty")).toBeNull()
  })

  it("hands a mounted slot the slug and the live session token", async () => {
    const seen: PortalSlotProps[] = []
    registerPortalSlot({
      id: "fake-slot",
      Component: (props: PortalSlotProps) => {
        seen.push(props)
        return <div>Fake slot content</div>
      },
    })
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "active", sessionToken: "rotated-token" })

    render(<PortalShell slug="example-therapy" />)
    await screen.findByText("Fake slot content")

    expect(seen[0]).toEqual({ slug: "example-therapy", sessionToken: "rotated-token" })
  })

  it("hands a slot the token minted by a redeem, not a stale one", async () => {
    const seen: PortalSlotProps[] = []
    registerPortalSlot({
      id: "fake-slot",
      Component: (props: PortalSlotProps) => {
        seen.push(props)
        return <div>Fake slot content</div>
      },
    })
    searchParams = new URLSearchParams({ invite: "tok-1" })
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })
    redeemAndStore.mockResolvedValue({ ok: true, sessionToken: "minted-token" })
    const user = userEvent.setup()

    render(<PortalShell slug="example-therapy" />)
    await screen.findByTestId("portal-shell-otp")
    await user.type(screen.getByTestId("portal-shell-otp-input"), "123456")
    await user.click(screen.getByTestId("portal-shell-otp-submit"))

    await screen.findByText("Fake slot content")
    expect(seen[0]).toEqual({ slug: "example-therapy", sessionToken: "minted-token" })
  })

  it("renders the expired/revoked note when a stored session's refresh fails", async () => {
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "expired" })

    render(<PortalShell slug="example-therapy" />)

    expect(await screen.findByTestId("portal-shell-no-session")).toBeTruthy()
    expect(screen.getByTestId("portal-shell-expired-note")).toBeTruthy()
  })

  it.each([
    ["bad code", { ok: false }],
    ["expired invite", { ok: false }],
    ["attempt-capped", { ok: false }],
    ["already redeemed", { ok: false }],
  ])("every redeem failure (%s) renders the same generic error", async (_label, failure) => {
    searchParams = new URLSearchParams({ invite: "tok-1" })
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })
    redeemAndStore.mockResolvedValue(failure)
    const user = userEvent.setup()

    render(<PortalShell slug="example-therapy" />)
    await screen.findByTestId("portal-shell-otp")

    await user.type(screen.getByTestId("portal-shell-otp-input"), "123456")
    await user.click(screen.getByTestId("portal-shell-otp-submit"))

    await waitFor(() => {
      expect(screen.getByTestId("portal-shell-otp-error").textContent).toBe(
        "That code didn't work. Check it and try again, or ask your practice for a new invite link.",
      )
    })
  })

  it("a successful redeem moves the shell to the active state", async () => {
    searchParams = new URLSearchParams({ invite: "tok-1" })
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })
    redeemAndStore.mockResolvedValue({ ok: true, sessionToken: "session-1" })
    const user = userEvent.setup()

    render(<PortalShell slug="example-therapy" />)
    await screen.findByTestId("portal-shell-otp")

    await user.type(screen.getByTestId("portal-shell-otp-input"), "123456")
    await user.click(screen.getByTestId("portal-shell-otp-submit"))

    expect(await screen.findByTestId("portal-shell-active")).toBeTruthy()
    expect(redeemAndStore).toHaveBeenCalledWith("example-therapy", "tok-1", "123456")
  })
})
