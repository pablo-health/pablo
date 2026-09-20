// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Coming back from Zoom with an authorization code on the URL.
 *
 * This covers the join rather than either end of it. Both ends already had
 * tests and both passed while the flow was broken: the API route was
 * exercised directly, the card was exercised against a mocked client, and
 * nothing asserted that approving at Zoom ever caused the code to be spent.
 * So every test here mounts the real card at a URL carrying a code and
 * asserts what the clinician ends up looking at.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { TelehealthProviders } from "@/lib/api/telehealth"
import { TelehealthSettings } from "../TelehealthSettings"

let searchParams = new URLSearchParams()
const routerReplace = vi.fn()
// One stable object: a fresh router identity per render would restart the
// effect that depends on it, which is not how next/navigation behaves.
const router = { replace: routerReplace, push: vi.fn() }

vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useSearchParams: () => searchParams,
}))

const SIGNED_IN = { user: { uid: "u1" }, loading: false }
let authState: { user: { uid: string } | null; loading: boolean } = SIGNED_IN

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ ...authState, getIdToken: async () => "token" }),
}))

const listProviders = vi.fn<() => Promise<TelehealthProviders>>()
const completeConnect = vi.fn()
const getAuthUrl = vi.fn()

vi.mock("@/lib/api/telehealth", () => ({
  listTelehealthProviders: () => listProviders(),
  completeZoomConnect: (...args: unknown[]) => completeConnect(...args),
  getZoomAuthUrl: (...args: unknown[]) => getAuthUrl(...args),
  disconnectZoom: vi.fn(),
  setTelehealthRoomUrl: vi.fn(),
}))

const RETURN_URL = "http://localhost:3000/dashboard/settings/sessions"

function providers(zoomConnected: boolean): TelehealthProviders {
  return {
    providers: [{ id: "zoom", display_name: "Zoom", connected: zoomConnected }],
    default_provider: zoomConnected ? "zoom" : null,
    room_url: null,
    join_window_before_minutes: 15,
  }
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <TelehealthSettings />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  authState = SIGNED_IN
  searchParams = new URLSearchParams()
  window.history.replaceState({}, "", "/dashboard/settings/sessions")
  listProviders.mockResolvedValue(providers(false))
  completeConnect.mockResolvedValue({ connected: true, account_handle: "practice@example.test" })
})

describe("returning from Zoom", () => {
  it("spends the code, and the card ends up showing the account connected", async () => {
    searchParams = new URLSearchParams({ code: "zoom-code-1", state: "signed-state" })
    // Disconnected on the first read, connected once the exchange lands —
    // which is the transition the clinician is waiting to see.
    listProviders.mockResolvedValueOnce(providers(false)).mockResolvedValue(providers(true))

    renderCard()

    await waitFor(() =>
      expect(completeConnect).toHaveBeenCalledWith("zoom-code-1", "signed-state", RETURN_URL),
    )
    await waitFor(() =>
      expect(screen.getByTestId("telehealth-provider-zoom-state")).toHaveTextContent("Ready"),
    )
    expect(screen.getByTestId("telehealth-zoom-disconnect")).toBeInTheDocument()
  })

  it("clears the one-use code off the URL afterwards", async () => {
    searchParams = new URLSearchParams({ code: "zoom-code-1", state: "signed-state" })

    renderCard()

    await waitFor(() =>
      expect(routerReplace).toHaveBeenCalledWith("/dashboard/settings/sessions"),
    )
  })

  it("spends a code once even if the card re-renders", async () => {
    searchParams = new URLSearchParams({ code: "zoom-code-1", state: "signed-state" })
    listProviders.mockResolvedValueOnce(providers(false)).mockResolvedValue(providers(true))

    renderCard()

    await waitFor(() => expect(completeConnect).toHaveBeenCalledTimes(1))
    await waitFor(() =>
      expect(screen.getByTestId("telehealth-provider-zoom-state")).toHaveTextContent("Ready"),
    )
    // The refetch above re-renders the card; the code must not go again.
    expect(completeConnect).toHaveBeenCalledTimes(1)
  })

  it("waits for sign-in to settle before spending the code", async () => {
    // Coming back from Zoom is a full page load, and a child's effects run
    // before the provider that initialises auth. Exchanging then would send
    // a request with no credential and burn the code on a 401.
    authState = { user: null, loading: true }
    searchParams = new URLSearchParams({ code: "zoom-code-1", state: "signed-state" })

    renderCard()

    await screen.findByTestId("telehealth-settings")
    expect(completeConnect).not.toHaveBeenCalled()
  })

  it("says so when Zoom does not finish, and leaves the card disconnected", async () => {
    searchParams = new URLSearchParams({ code: "zoom-code-1", state: "signed-state" })
    completeConnect.mockRejectedValue(new Error("bad state"))

    renderCard()

    expect(await screen.findByTestId("telehealth-zoom-connect-error")).toBeInTheDocument()
    expect(screen.getByTestId("telehealth-provider-zoom-state")).toHaveTextContent("Not set up")
  })
})

describe("leaving for Zoom", () => {
  it("asks to come back to the section that can read the code", async () => {
    // Not /dashboard/settings, which has no page of its own and
    // server-redirects to the first item — dropping the query string, and
    // the authorization code with it, before anything could read it. The
    // path sent here and the path the exchange is made from have to agree.
    const assign = vi.fn()
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, origin: "http://localhost:3000", assign },
    })
    getAuthUrl.mockResolvedValue({ auth_url: "https://zoom.example.test/oauth/authorize?x=1" })

    renderCard()
    await userEvent.click(await screen.findByTestId("telehealth-zoom-connect"))

    await waitFor(() => expect(getAuthUrl).toHaveBeenCalledWith(RETURN_URL))
    expect(assign).toHaveBeenCalledWith("https://zoom.example.test/oauth/authorize?x=1")
  })
})

describe("an ordinary visit to the settings page", () => {
  it("spends nothing when there is no code on the URL", async () => {
    renderCard()

    await screen.findByTestId("telehealth-provider-zoom")
    expect(completeConnect).not.toHaveBeenCalled()
    expect(routerReplace).not.toHaveBeenCalled()
    expect(screen.queryByTestId("telehealth-zoom-connect-error")).not.toBeInTheDocument()
  })
})
