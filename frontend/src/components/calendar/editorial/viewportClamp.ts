// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

const VIEWPORT_MARGIN = 12

/**
 * Clamp a fixed-position popover so it stays within the viewport.
 *
 * The lower-bound (Math.max) is applied last so it can never push the element
 * off the right or bottom edge when the viewport is narrower than the element.
 */
export function clampToViewport(
  preferredLeft: number,
  preferredTop: number,
  width: number,
  height: number,
): { left: number; top: number } {
  const left = Math.max(
    VIEWPORT_MARGIN,
    Math.min(preferredLeft, window.innerWidth - width - VIEWPORT_MARGIN),
  )
  const top = Math.max(
    VIEWPORT_MARGIN,
    Math.min(preferredTop, window.innerHeight - height - VIEWPORT_MARGIN),
  )
  return { left, top }
}
