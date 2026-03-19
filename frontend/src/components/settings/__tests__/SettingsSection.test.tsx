// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { SettingsSection } from "../SettingsSection"
import { Clock } from "lucide-react"

describe("SettingsSection", () => {
  it("renders title and description", () => {
    render(
      <SettingsSection icon={Clock} title="Working Hours" description="Set your hours.">
        <p>Content</p>
      </SettingsSection>
    )

    expect(screen.getByText("Working Hours")).toBeInTheDocument()
    expect(screen.getByText("Set your hours.")).toBeInTheDocument()
  })

  it("renders children", () => {
    render(
      <SettingsSection icon={Clock} title="Test" description="Desc">
        <button>Click me</button>
      </SettingsSection>
    )

    expect(screen.getByRole("button", { name: "Click me" })).toBeInTheDocument()
  })

  it("uses section landmark with aria-labelledby", () => {
    render(
      <SettingsSection icon={Clock} title="Profile" description="Your profile.">
        <p>Content</p>
      </SettingsSection>
    )

    const section = screen.getByRole("region", { name: "Profile" })
    expect(section).toBeInTheDocument()
  })
})
