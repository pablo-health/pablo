// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The setup page's one job beyond mounting the wizard: knowing where finishing
 * goes. The wizard cannot know — it does not know what page it was mounted on
 * — so a missing exit here shows up as a Finish button that does nothing.
 */

import { render, screen } from "@testing-library/react"
import { fireEvent } from "@testing-library/dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import BillingSetupPage from "../page"

const push = vi.hoisted(() => vi.fn())

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}))

vi.mock("@/components/billing/setup/GetPaidWizard", () => ({
  GetPaidWizard: ({ onSettled }: { onSettled?: () => void }) => (
    <button type="button" onClick={onSettled}>
      finish
    </button>
  ),
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe("BillingSetupPage", () => {
  it("sends her to billing when setup is settled", () => {
    render(<BillingSetupPage />)

    fireEvent.click(screen.getByRole("button", { name: "finish" }))

    expect(push).toHaveBeenCalledWith("/dashboard/billing")
  })
})
