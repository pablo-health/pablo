// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { renderHook, screen, waitFor } from "@testing-library/react"
import {
  _resetSupportEmailLookupForTests,
  supportEmailFrom,
  supportMailto,
  useSupportEmail,
} from "@/lib/support"
import { renderWithProviders } from "@/test/renderWithProviders"
import { testRuntimeConfig } from "@/test/runtimeConfig"

function Probe() {
  return <div>support:{useSupportEmail() ?? "none"}</div>
}

describe("supportEmailFrom", () => {
  it("accepts an ordinary address and trims it", () => {
    expect(supportEmailFrom("  help@clinic.example ")).toBe("help@clinic.example")
  })

  it.each([undefined, null, "", "   "])("treats %j as no address", (value) => {
    expect(supportEmailFrom(value)).toBeNull()
  })

  // A typo in the deployment's env must not turn into a link that goes nowhere,
  // and nothing in the value may smuggle extra mailto parameters into the href.
  it.each(["help", "help@clinic", "help @clinic.example", "a@b.c?cc=x@y.z", "<a@b.c>"])(
    "rejects the malformed value %j",
    (value) => {
      expect(supportEmailFrom(value)).toBeNull()
    },
  )
})

describe("supportMailto", () => {
  it("builds a mailto href for the address", () => {
    expect(supportMailto("help@clinic.example")).toBe("mailto:help@clinic.example")
  })
})

describe("useSupportEmail", () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    _resetSupportEmailLookupForTests()
    fetchMock.mockReset().mockRejectedValue(new Error("offline"))
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("reads the address from the runtime config", async () => {
    renderWithProviders(<Probe />, {
      config: testRuntimeConfig({ supportEmail: "help@clinic.example" }),
    })
    expect(await screen.findByText("support:help@clinic.example")).toBeInTheDocument()
  })

  it("is null when the deployment configured none", async () => {
    renderWithProviders(<Probe />, { config: testRuntimeConfig({ supportEmail: "" }) })
    expect(await screen.findByText("support:none")).toBeInTheDocument()
  })

  // Error fallbacks call this; throwing there would replace one failure with another.
  it("is null, without throwing, outside ConfigProvider", async () => {
    const { result } = renderHook(() => useSupportEmail())
    expect(result.current).toBeNull()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/config"))
    expect(result.current).toBeNull()
  })

  // The root error boundary replaces every provider, and it is the screen
  // where someone most needs to know who to write to.
  it("reads /api/config itself outside ConfigProvider", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ supportEmail: "help@clinic.example" }),
    })
    const { result } = renderHook(() => useSupportEmail())
    await waitFor(() => expect(result.current).toBe("help@clinic.example"))
  })

  it("does not fetch when the provider already has the config", async () => {
    renderWithProviders(<Probe />, {
      config: testRuntimeConfig({ supportEmail: "help@clinic.example" }),
    })
    await screen.findByText("support:help@clinic.example")
    expect(fetchMock).not.toHaveBeenCalledWith("/api/config")
  })
})
