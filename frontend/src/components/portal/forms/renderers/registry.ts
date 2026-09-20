// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Which renderer draws which kind of question.
 *
 * Keyed by `item_type`, the same vocabulary `backend/app/intake/items.py`
 * constrains. A type with no entry here renders as a step still to come, so
 * a practice that puts a question on a form this portal cannot ask yet gets
 * a patient who is told so rather than a blank screen.
 *
 * **Which types are here is not an oversight.** A question the practice
 * writes itself — a free-text box, a set of choices, a scale — has nowhere
 * to store its wording yet: `intake_item_definitions` carries a key, a type
 * and the type's settings, and no prompt. So there is nothing to put on the
 * screen above the control, and a renderer for one would have to invent the
 * question. The three here are the three whose wording the engine owns and
 * serves: who you are, what brings you in, and a measure's published items.
 * The rest arrive with the child that gives them a prompt to show.
 *
 * The server draws the same line from the other side: it refuses an answer
 * to a file-backed item with a 422, and a form still needing one cannot be
 * handed in. So nothing here can let a patient believe a form is finished
 * when it is not — the "is it finished" question is only ever asked of the
 * server.
 */

import { demographicsRenderer } from "./DemographicsItem"
import { instructionsRenderer, sectionRenderer, unavailableRenderer } from "./DisplayItem"
import { instrumentRenderer } from "./InstrumentItem"
import { reasonRenderer } from "./ReasonItem"
import type { ItemRenderer } from "./types"

const RENDERERS: Record<string, ItemRenderer> = {
  demographics: demographicsRenderer,
  reason: reasonRenderer,
  instrument: instrumentRenderer,
  section: sectionRenderer,
  instructions: instructionsRenderer,
}

/** The renderer for one item type; the "available soon" one for the rest. */
export function rendererFor(itemType: string): ItemRenderer {
  return RENDERERS[itemType] ?? unavailableRenderer
}

/** The item types this portal can draw. Exported for the tests to walk. */
export const RENDERED_ITEM_TYPES = Object.keys(RENDERERS)
