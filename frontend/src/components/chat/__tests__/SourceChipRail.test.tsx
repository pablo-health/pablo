// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SourceChipRail tests (PABLO-6x5.9).
 *
 * The rail must only surface sources with a live data path — no chips
 * for always-empty note types or the broken 'Selected sessions', whether
 * they arrive via the active selection or the "Add source" menu.
 */

import { describe, it, expect, vi } from "vitest"
import { fireEvent, render, screen, within } from "@testing-library/react"

import { SourceChipRail } from "../SourceChipRail"
import type { SourceSelection } from "@/lib/chat/types"

function renderRail(selection: SourceSelection) {
  return render(
    <SourceChipRail
      selection={selection}
      latestManifest={null}
      onToggle={vi.fn()}
      onOpenDetail={vi.fn()}
      onAdd={vi.fn()}
    />,
  )
}

describe("SourceChipRail — supported sources only", () => {
  it("renders chips only for supported active sources", () => {
    // A stale/unsupported key left in an older selection must not render.
    renderRail({
      progress_notes_recent: { limit: 3 },
      most_recent_intake: true,
      progress_notes_explicit: { note_ids: ["n1"] },
    })

    expect(screen.getByText("Progress notes")).toBeInTheDocument()
    expect(screen.queryByText("Intake")).not.toBeInTheDocument()
    expect(screen.queryByText("Selected sessions")).not.toBeInTheDocument()
  })

  it("offers only supported, not-yet-selected sources in the add menu", () => {
    renderRail({ progress_notes_recent: { limit: 3 } })

    fireEvent.click(screen.getByRole("button", { name: /add source/i }))
    const menu = screen.getByRole("menu")

    // The two remaining supported sources are addable.
    expect(within(menu).getByText("Uploaded documents")).toBeInTheDocument()
    expect(within(menu).getByText("Pasted text")).toBeInTheDocument()

    // Unsupported sources are never offered.
    expect(within(menu).queryByText("Selected sessions")).not.toBeInTheDocument()
    expect(within(menu).queryByText("Medications")).not.toBeInTheDocument()
    expect(within(menu).queryByText("Treatment plan")).not.toBeInTheDocument()
    expect(within(menu).queryByText("Labs")).not.toBeInTheDocument()
  })
})
