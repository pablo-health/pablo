// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { renderHook, screen } from "@testing-library/react"
import { supportEmailFrom, supportMailto, useSupportEmail } from "@/lib/support"
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
  it("is null, without throwing, outside ConfigProvider", () => {
    const { result } = renderHook(() => useSupportEmail())
    expect(result.current).toBeNull()
  })
})
