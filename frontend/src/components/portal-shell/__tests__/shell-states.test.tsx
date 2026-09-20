// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PortalShell — every state the shell can be in.
 *
 * Pins the state machine driven entirely off the mocked
 * `@/lib/portal-shell/{api,session}` fetchers: resolving, unknown practice,
 * no-session, code entry (when the URL carries an invitation), the active
 * shell's zero-slot empty card, and the expired/revoked note. A mounted
 * slot is handed the slug and the LIVE session token, including the one a
 * redeem just minted.
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

/** Open the page the way an invitation link does. */
function arriveWithInvitation(token: string, path = "/portal/example-therapy"): void {
  window.history.replaceState(null, "", `${path}#token=${token}`)
}

const REDEEMED = {
  ok: true,
  session: {
    sessionToken: "session-1",
    practiceSlug: "example-therapy",
    practiceDisplayName: "Example Therapy",
  },
}

beforeEach(() => {
  vi.clearAllMocks()
  window.history.replaceState(null, "", "/portal/example-therapy")
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

  it("renders the code form when the URL carries an invitation and there is no session", async () => {
    arriveWithInvitation("tok-1")
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
    arriveWithInvitation("tok-1")
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })
    redeemAndStore.mockResolvedValue({
      ok: true,
      session: {
        sessionToken: "minted-token",
        practiceSlug: "example-therapy",
        practiceDisplayName: "Example Therapy",
      },
    })
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
    arriveWithInvitation("tok-1")
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
    arriveWithInvitation("tok-1")
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })
    redeemAndStore.mockResolvedValue(REDEEMED)
    const user = userEvent.setup()

    render(<PortalShell slug="example-therapy" />)
    await screen.findByTestId("portal-shell-otp")

    await user.type(screen.getByTestId("portal-shell-otp-input"), "123456")
    await user.click(screen.getByTestId("portal-shell-otp-submit"))

    expect(await screen.findByTestId("portal-shell-active")).toBeTruthy()
    expect(redeemAndStore).toHaveBeenCalledWith("tok-1", "123456")
  })
})

/**
 * Arriving on an invitation link.
 *
 * The token rides in the URL fragment, which the server never sees, so
 * everything about picking it up and putting it back down is the shell's
 * job: spend it, take it out of the address bar, and end up on the
 * practice's own address either way — including from the older link that
 * names no practice at all and learns which one from the response.
 */
describe("arriving with an invitation", () => {
  const user = () => userEvent.setup()

  async function enterTheCode() {
    const typing = user()
    await screen.findByTestId("portal-shell-otp")
    await typing.type(screen.getByTestId("portal-shell-otp-input"), "123456")
    await typing.click(screen.getByTestId("portal-shell-otp-submit"))
  }

  it("takes the invitation out of the URL once it is spent", async () => {
    arriveWithInvitation("tok-1")
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })
    redeemAndStore.mockResolvedValue(REDEEMED)

    render(<PortalShell slug="example-therapy" />)
    await enterTheCode()

    await screen.findByTestId("portal-shell-active")
    expect(window.location.hash).toBe("")
    expect(window.location.pathname).toBe("/portal/example-therapy")
  })

  it("lands on the practice the response names when the path names none", async () => {
    arriveWithInvitation("tok-1", "/portal/redeem")
    redeemAndStore.mockResolvedValue(REDEEMED)

    render(<PortalShell slug="redeem" />)
    await enterTheCode()

    expect(await screen.findByTestId("portal-shell-active")).toBeTruthy()
    expect(screen.getByTestId("portal-shell-practice-name").textContent).toBe("Example Therapy")
    expect(window.location.pathname).toBe("/portal/example-therapy")
    expect(window.location.hash).toBe("")
    // Nothing to resolve: that path names no practice.
    expect(resolvePortalPractice).not.toHaveBeenCalled()
  })

  it("hands a slot the practice from the response, not the one in the path", async () => {
    const seen: PortalSlotProps[] = []
    registerPortalSlot({
      id: "fake-slot",
      Component: (props: PortalSlotProps) => {
        seen.push(props)
        return <div>Fake slot content</div>
      },
    })
    arriveWithInvitation("tok-1", "/portal/redeem")
    redeemAndStore.mockResolvedValue(REDEEMED)

    render(<PortalShell slug="redeem" />)
    await enterTheCode()
    await screen.findByText("Fake slot content")

    expect(seen[0]).toEqual({ slug: "example-therapy", sessionToken: "session-1" })
  })

  it("shows the generic state on that path with no invitation at all", async () => {
    render(<PortalShell slug="redeem" />)

    expect(await screen.findByTestId("portal-shell-unknown")).toBeTruthy()
  })

  it("does not let an invitation rescue a slug that resolves to nothing", async () => {
    arriveWithInvitation("tok-1", "/portal/never-existed")
    resolvePortalPractice.mockResolvedValue({ ok: false })

    render(<PortalShell slug="never-existed" />)

    expect(await screen.findByTestId("portal-shell-unknown")).toBeTruthy()
    expect(screen.queryByTestId("portal-shell-otp")).toBeNull()
    expect(redeemAndStore).not.toHaveBeenCalled()
  })

  it("ignores a fragment that carries something other than an invitation", async () => {
    window.history.replaceState(null, "", "/portal/example-therapy#section=billing")
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })

    render(<PortalShell slug="example-therapy" />)

    expect(await screen.findByTestId("portal-shell-no-session")).toBeTruthy()
  })
})
