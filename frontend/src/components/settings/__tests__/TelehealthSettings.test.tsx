// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The video-sessions card: what it offers, and what it refuses to save.
 *
 * The interesting assertion is the second one in each pair. A service the
 * deployment offers but the clinician has not connected has to be VISIBLE and
 * marked not set up — leaving it out would be a screen that says nothing
 * about the thing the reader came to do.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import * as api from "@/lib/api/telehealth"
import { TelehealthSettings } from "../TelehealthSettings"

vi.mock("@/lib/api/telehealth")

// The card carries the return leg of the Zoom connect flow, which reads the
// query string and clears it afterwards. None of the cases below arrive with
// a code on the URL, so this is here to let the card mount rather than to be
// exercised — the round trip itself is covered in ZoomConnectReturn.test.tsx.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: { uid: "u1" }, loading: false, getIdToken: async () => "token" }),
}))

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
  vi.mocked(api.listTelehealthProviders).mockResolvedValue({
    providers: [
      { id: "zoom", display_name: "Zoom", connected: false },
      { id: "doxy_me", display_name: "Doxy.me", connected: false },
      { id: "manual", display_name: "A link I'll paste in", connected: true },
    ],
    default_provider: null,
    room_url: null,
    join_window_before_minutes: 15,
  })
  vi.mocked(api.setTelehealthRoomUrl).mockResolvedValue({ room_url: null })
  vi.mocked(api.disconnectZoom).mockResolvedValue({ connected: false, account_handle: null })
})

describe("what the card shows", () => {
  it("lists every service the deployment offers, connected or not", async () => {
    renderCard()

    expect(await screen.findByTestId("telehealth-provider-zoom")).toBeInTheDocument()
    expect(screen.getByTestId("telehealth-provider-doxy_me")).toBeInTheDocument()
    expect(screen.getByTestId("telehealth-provider-manual")).toBeInTheDocument()
  })

  it("says which are ready and which are not set up", async () => {
    renderCard()

    expect(await screen.findByTestId("telehealth-provider-zoom-state")).toHaveTextContent(
      "Not set up",
    )
    expect(screen.getByTestId("telehealth-provider-manual-state")).toHaveTextContent("Ready")
  })

  it("offers Connect Zoom when Zoom is not connected", async () => {
    renderCard()

    expect(await screen.findByTestId("telehealth-zoom-connect")).toBeInTheDocument()
    expect(screen.queryByTestId("telehealth-zoom-disconnect")).not.toBeInTheDocument()
  })

  it("offers Disconnect once it is", async () => {
    vi.mocked(api.listTelehealthProviders).mockResolvedValue({
      providers: [{ id: "zoom", display_name: "Zoom", connected: true }],
      default_provider: "zoom",
      room_url: null,
      join_window_before_minutes: 15,
    })
    renderCard()

    expect(await screen.findByTestId("telehealth-zoom-disconnect")).toBeInTheDocument()
    expect(screen.queryByTestId("telehealth-zoom-connect")).not.toBeInTheDocument()
  })

  it("shows nothing about a service the deployment does not offer", async () => {
    vi.mocked(api.listTelehealthProviders).mockResolvedValue({
      providers: [{ id: "manual", display_name: "A link I'll paste in", connected: true }],
      default_provider: null,
      room_url: null,
      join_window_before_minutes: 15,
    })
    renderCard()

    await screen.findByTestId("telehealth-provider-manual")
    expect(screen.queryByTestId("telehealth-zoom-connect")).not.toBeInTheDocument()
    expect(screen.queryByTestId("telehealth-room-url")).not.toBeInTheDocument()
  })
})

describe("the clinician's own room", () => {
  it("is seeded with what is already saved", async () => {
    vi.mocked(api.listTelehealthProviders).mockResolvedValue({
      providers: [{ id: "doxy_me", display_name: "Doxy.me", connected: true }],
      default_provider: "doxy_me",
      room_url: "https://clinic.example.test/me",
      join_window_before_minutes: 15,
    })
    renderCard()

    await waitFor(() =>
      expect(screen.getByTestId("telehealth-room-url")).toHaveValue(
        "https://clinic.example.test/me",
      ),
    )
  })

  it("saves a web address", async () => {
    renderCard()
    await screen.findByTestId("telehealth-room-url")

    await userEvent.type(
      screen.getByTestId("telehealth-room-url"),
      "https://clinic.example.test/me",
    )
    await userEvent.click(screen.getByTestId("telehealth-room-url-save"))

    await waitFor(() =>
      expect(api.setTelehealthRoomUrl).toHaveBeenCalledWith("https://clinic.example.test/me"),
    )
  })

  it("refuses anything that is not one, and says so", async () => {
    renderCard()
    await screen.findByTestId("telehealth-room-url")

    await userEvent.type(screen.getByTestId("telehealth-room-url"), "javascript:alert(1)")
    await userEvent.click(screen.getByTestId("telehealth-room-url-save"))

    expect(await screen.findByTestId("telehealth-settings-error")).toBeInTheDocument()
    expect(api.setTelehealthRoomUrl).not.toHaveBeenCalled()
  })

  it("clearing it removes the room rather than saving an empty string", async () => {
    vi.mocked(api.listTelehealthProviders).mockResolvedValue({
      providers: [{ id: "doxy_me", display_name: "Doxy.me", connected: true }],
      default_provider: "doxy_me",
      room_url: "https://clinic.example.test/me",
      join_window_before_minutes: 15,
    })
    renderCard()
    await waitFor(() => expect(screen.getByTestId("telehealth-room-url")).toHaveValue(
      "https://clinic.example.test/me",
    ))

    await userEvent.clear(screen.getByTestId("telehealth-room-url"))
    await userEvent.click(screen.getByTestId("telehealth-room-url-save"))

    await waitFor(() => expect(api.setTelehealthRoomUrl).toHaveBeenCalledWith(null))
  })
})
