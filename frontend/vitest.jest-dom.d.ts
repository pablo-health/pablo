// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

// Puts the jest-dom matchers (toBeInTheDocument, toHaveAttribute, ...) on
// vitest's `expect`. vitest 5 gave `Assertion` a second type parameter, and
// jest-dom 7's own augmentation still declares it with one, so TypeScript
// cannot merge the two and every matcher disappears. `Matchers<R, T>` is the
// interface vitest 5 exposes for exactly this. Delete this file once jest-dom
// ships an augmentation that matches vitest 5.
import "vitest"
import type { TestingLibraryMatchers } from "@testing-library/jest-dom/matchers"

declare module "vitest" {
  // Both type parameters must match vitest's declaration for the merge to apply.
  interface Matchers<R extends void | Promise<void> = void | Promise<void>, T = unknown>
    extends TestingLibraryMatchers<unknown, R> {}
}
