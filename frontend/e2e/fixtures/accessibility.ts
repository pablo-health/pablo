// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import AxeBuilder from "@axe-core/playwright"
import type { Page } from "@playwright/test"
import { expect } from "@playwright/test"

type Violation = {
  id: string
  impact: string | null | undefined
  targets: unknown[]
}

/** Fail with a compact, actionable list instead of Axe's full result payload. */
export async function expectNoAccessibilityViolations(page: Page): Promise<void> {
  const { violations } = await new AxeBuilder({ page }).analyze()
  const summary: Violation[] = violations.map(({ id, impact, nodes }) => ({
    id,
    impact,
    targets: nodes.map(({ target }) => target),
  }))

  expect(summary).toEqual([])
}
