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
import { PortalHome } from "../PortalHome"
import { PortalSection } from "../PortalSection"
import { PortalShell } from "../PortalShell"
import { resetPortalSlotsForTests, registerPortalSlot, type PortalSlotProps } from "../slots"

const replace = vi.fn()

// The shell reads which page it is on from the router; here the address bar
// is the router, so a test moves between pages with `history.replaceState`.
vi.mock("next/navigation", () => ({
  usePathname: () => window.location.pathname,
  useRouter: () => ({ replace }),
}))

const resolvePortalPractice = vi.fn()
const fetchCapabilities = vi.fn()
const bootstrapSession = vi.fn()
const redeemAndStore = vi.fn()
const signOutAndForget = vi.fn()
const requestSignInCode = vi.fn()

vi.mock("@/lib/portal-shell/api", () => ({
  resolvePortalPractice: (...args: unknown[]) => resolvePortalPractice(...args),
  fetchCapabilities: (...args: unknown[]) => fetchCapabilities(...args),
  requestSignInCode: (...args: unknown[]) => requestSignInCode(...args),
}))

vi.mock("@/lib/portal-shell/session", () => ({
  bootstrapSession: (...args: unknown[]) => bootstrapSession(...args),
  redeemAndStore: (...args: unknown[]) => redeemAndStore(...args),
  signOutAndForget: (...args: unknown[]) => signOutAndForget(...args),
}))

/** Open the page the way an invitation link does. */
function arriveWithInvitation(token: string, path = "/portal/example-therapy"): void {
  window.history.replaceState(null, "", `${path}#invite=${token}`)
}

const REDEEMED = { ok: true, sessionToken: "session-1" }

/** Tap "Text me a code", then type the code that arrived and continue. */
async function askForACodeAndEnterIt(user: ReturnType<typeof userEvent.setup>, code = "123456") {
  await user.click(await screen.findByTestId("portal-shell-request-code"))
  await user.type(await screen.findByTestId("portal-shell-otp-input"), code)
  await user.click(screen.getByTestId("portal-shell-otp-submit"))
}

beforeEach(() => {
  vi.clearAllMocks()
  requestSignInCode.mockResolvedValue({ ok: true })
  window.history.replaceState(null, "", "/portal/example-therapy")
  // The shell registers the engine's own modules when it is imported. A
  // spec about the shell's states drives its own slots instead.
  resetPortalSlotsForTests()
  // The capability fetch fires on every active phase. These cases are about
  // the state machine rather than about module gating, so it answers "could
  // not fetch" — which keeps every registered slot rendered. Module gating
  // has a spec of its own.
  fetchCapabilities.mockResolvedValue({ ok: false })
  signOutAndForget.mockResolvedValue({ revoked: true })
})

describe("PortalShell", () => {
  it("renders a skeleton while resolving", () => {
    resolvePortalPractice.mockReturnValue(new Promise(() => {}))

    render(<PortalShell slug="example-therapy"><PortalHome /></PortalShell>)

    expect(screen.getByTestId("portal-shell-skeleton")).toBeTruthy()
  })

  it("renders the generic unknown-practice state for an unresolvable slug", async () => {
    resolvePortalPractice.mockResolvedValue({ ok: false })

    render(<PortalShell slug="never-existed"><PortalHome /></PortalShell>)

    expect(await screen.findByTestId("portal-shell-unknown")).toBeTruthy()
  })

  it("renders the display name once the slug resolves", async () => {
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })

    render(<PortalShell slug="example-therapy"><PortalHome /></PortalShell>)

    expect(await screen.findByText("Example Therapy")).toBeTruthy()
  })

  it("renders the no-session empty state with no stored session and no invite", async () => {
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })

    render(<PortalShell slug="example-therapy"><PortalHome /></PortalShell>)

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

    render(<PortalShell slug="example-therapy"><PortalHome /></PortalShell>)

    expect(await screen.findByTestId("portal-shell-otp")).toBeTruthy()
  })

  it("offers to text a code before asking for one, and texts nothing on arrival", async () => {
    arriveWithInvitation("tok-1")
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })

    render(<PortalShell slug="example-therapy" />)

    const ask = await screen.findByTestId("portal-shell-request-code")
    expect(ask.textContent).toBe("Text me a code")
    expect(screen.queryByTestId("portal-shell-otp-input")).toBeNull()
    expect(requestSignInCode).not.toHaveBeenCalled()
  })

  it("asks for a code with the invitation, then shows the code field", async () => {
    arriveWithInvitation("tok-1")
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })
    const user = userEvent.setup()

    render(<PortalShell slug="example-therapy" />)
    await user.click(await screen.findByTestId("portal-shell-request-code"))

    expect(await screen.findByTestId("portal-shell-otp-input")).toBeTruthy()
    expect(requestSignInCode).toHaveBeenCalledWith("tok-1")
    expect(screen.getByTestId("portal-shell-resend-code").textContent).toBe("Send a new code")
  })

  it("sends a new code on request and says so", async () => {
    arriveWithInvitation("tok-1")
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })
    const user = userEvent.setup()

    render(<PortalShell slug="example-therapy" />)
    await user.click(await screen.findByTestId("portal-shell-request-code"))
    await user.click(await screen.findByTestId("portal-shell-resend-code"))

    expect((await screen.findByTestId("portal-shell-code-notice")).textContent).toBe(
      "We sent a new code.",
    )
    expect(requestSignInCode).toHaveBeenCalledTimes(2)
  })

  it("sends a link the server will not text a code for to sign in again, saying why", async () => {
    arriveWithInvitation("tok-1")
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })
    requestSignInCode.mockResolvedValue({ ok: false, reason: "refused" })
    const user = userEvent.setup()

    render(<PortalShell slug="example-therapy" />)
    await user.click(await screen.findByTestId("portal-shell-request-code"))

    expect(await screen.findByTestId("portal-shell-link-ended")).toHaveTextContent("That sign-in link has expired.")
    expect(screen.getByTestId("portal-recover-email")).toBeTruthy()
    expect(screen.getByRole("button", { name: "Email me a sign-in link" })).toBeTruthy()
  })

  it("keeps the patient on the first step when a code could not be sent", async () => {
    arriveWithInvitation("tok-1")
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })
    requestSignInCode.mockResolvedValue({ ok: false, reason: "unavailable" })
    const user = userEvent.setup()

    render(<PortalShell slug="example-therapy" />)
    await user.click(await screen.findByTestId("portal-shell-request-code"))

    expect((await screen.findByTestId("portal-shell-otp-error")).textContent).toBe(
      "We couldn't send a code. Try again in a moment.",
    )
    expect(screen.getByTestId("portal-shell-request-code")).toBeTruthy()
    expect(screen.queryByTestId("portal-shell-otp-input")).toBeNull()
  })

  it("renders the active shell's empty card when no slots are registered", async () => {
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "active", sessionToken: "session-1" })

    render(<PortalShell slug="example-therapy"><PortalHome /></PortalShell>)

    expect(await screen.findByTestId("portal-shell-active")).toBeTruthy()
    expect(await screen.findByTestId("portal-shell-empty")).toBeTruthy()
  })

  it("shows a registered slot as a tile on Home, not its content", async () => {
    registerPortalSlot({ id: "fake-slot", Component: () => <div>Fake slot content</div> })
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "active", sessionToken: "session-1" })

    render(<PortalShell slug="example-therapy"><PortalHome /></PortalShell>)

    expect(await screen.findByTestId("portal-home-tile-fake-slot")).toBeTruthy()
    expect(screen.queryByText("Fake slot content")).toBeNull()
    expect(screen.queryByTestId("portal-shell-empty")).toBeNull()
  })

  it("renders a registered slot on its own page", async () => {
    registerPortalSlot({ id: "fake-slot", Component: () => <div>Fake slot content</div> })
    window.history.replaceState(null, "", "/portal/example-therapy/fake-slot")
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "active", sessionToken: "session-1" })

    render(
      <PortalShell slug="example-therapy">
        <PortalSection id="fake-slot" />
      </PortalShell>,
    )

    expect(await screen.findByText("Fake slot content")).toBeTruthy()
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
    window.history.replaceState(null, "", "/portal/example-therapy/fake-slot")

    render(
      <PortalShell slug="example-therapy">
        <PortalSection id="fake-slot" />
      </PortalShell>,
    )
    await screen.findByText("Fake slot content")

    expect(seen[0]).toEqual({ slug: "example-therapy", sessionToken: "rotated-token" })
  })

  it("hands a slot the token minted by a redeem, not a stale one", async () => {
    // A redeem lands on Home, so what a slot first meets there is its tile's
    // summary.
    const seen: PortalSlotProps[] = []
    registerPortalSlot({
      id: "fake-slot",
      Component: () => <div>Fake slot content</div>,
      Summary: (props: PortalSlotProps) => {
        seen.push(props)
        return <>Fake slot content</>
      },
    })
    arriveWithInvitation("tok-1")
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })
    redeemAndStore.mockResolvedValue({ ok: true, sessionToken: "minted-token" })
    const user = userEvent.setup()

    render(<PortalShell slug="example-therapy"><PortalHome /></PortalShell>)
    await askForACodeAndEnterIt(user)

    await screen.findByText("Fake slot content")
    expect(seen[0]).toEqual({ slug: "example-therapy", sessionToken: "minted-token" })
  })

  it("renders the expired/revoked note when a stored session's refresh fails", async () => {
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "expired" })

    render(<PortalShell slug="example-therapy"><PortalHome /></PortalShell>)

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

    render(<PortalShell slug="example-therapy"><PortalHome /></PortalShell>)
    await askForACodeAndEnterIt(user)

    await waitFor(() => {
      expect(screen.getByTestId("portal-shell-otp-error").textContent).toBe(
        "That code didn't work. Check it and try again, or send a new code.",
      )
    })
    expect(screen.getByTestId("portal-shell-otp-recover")).toHaveAttribute(
      "href",
      "/portal/example-therapy/recover",
    )
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

    render(<PortalShell slug="example-therapy"><PortalHome /></PortalShell>)
    await askForACodeAndEnterIt(user)

    expect(await screen.findByTestId("portal-shell-active")).toBeTruthy()
    expect(redeemAndStore).toHaveBeenCalledWith("example-therapy", "tok-1", "123456")
  })
})

/**
 * Arriving on an invitation link.
 *
 * The token rides in the URL fragment, which the server never sees, so
 * picking it up and putting it back down is entirely the shell's job: read
 * it off the fragment, spend it, and leave the practice's address in the
 * bar with no credential on it.
 */
describe("arriving with an invitation", () => {
  const user = () => userEvent.setup()

  async function enterTheCode() {
    await askForACodeAndEnterIt(user())
  }

  it("takes the invitation out of the URL once it is spent", async () => {
    arriveWithInvitation("tok-1")
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })
    redeemAndStore.mockResolvedValue(REDEEMED)

    render(<PortalShell slug="example-therapy"><PortalHome /></PortalShell>)
    await enterTheCode()

    await screen.findByTestId("portal-shell-active")
    expect(window.location.hash).toBe("")
    expect(window.location.pathname).toBe("/portal/example-therapy")
  })

  it("keeps the query string while dropping the invitation", async () => {
    window.history.replaceState(null, "", "/portal/example-therapy?ref=email#invite=tok-1")
    resolvePortalPractice.mockResolvedValue({
      ok: true,
      data: { slug: "example-therapy", display_name: "Example Therapy" },
    })
    bootstrapSession.mockResolvedValue({ status: "none" })
    redeemAndStore.mockResolvedValue(REDEEMED)

    render(<PortalShell slug="example-therapy"><PortalHome /></PortalShell>)
    await enterTheCode()

    await screen.findByTestId("portal-shell-active")
    expect(window.location.hash).toBe("")
    expect(window.location.search).toBe("?ref=email")
  })

  it("does not let an invitation rescue a slug that resolves to nothing", async () => {
    arriveWithInvitation("tok-1", "/portal/never-existed")
    resolvePortalPractice.mockResolvedValue({ ok: false })

    render(<PortalShell slug="never-existed"><PortalHome /></PortalShell>)

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

    render(<PortalShell slug="example-therapy"><PortalHome /></PortalShell>)

    expect(await screen.findByTestId("portal-shell-no-session")).toBeTruthy()
  })
})
