// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Which renderer draws which kind of question.
 *
 * Keyed by `item_type`, the same vocabulary `backend/app/intake/items.py`
 * constrains. A type with no entry here renders as a step still to come, so
 * a practice that puts a question on a form this portal cannot ask yet gets
 * a patient who is told so rather than a blank screen.
 *
 * **Which types are missing is not an oversight.** Three are file-backed —
 * a consent to sign, a photo of an insurance card, any other upload — and
 * nothing stores a file yet; the save route refuses an answer to one. The
 * other two, an emergency contact and a guardian, are standard blocks of
 * fields whose screens have not been built.
 *
 * What is here divides into two, and the difference is where the question
 * comes from. Demographics, reason and a measure are asked in wording the
 * engine owns and serves, so those renderers read the form response.
 * Everything else is a question the practice wrote, and carries it on the
 * item as `label` and `help_text` — which is why those renderers share one
 * frame.
 *
 * The server draws the same line from the other side: it refuses an answer
 * to a file-backed item with a 422, and a form still needing one cannot be
 * handed in. So nothing here can let a patient believe a form is finished
 * when it is not — the "is it finished" question is only ever asked of the
 * server.
 */

import { multiChoiceRenderer, singleChoiceRenderer } from "./ChoiceItem"
import { dateRenderer } from "./DateItem"
import { demographicsRenderer } from "./DemographicsItem"
import { instructionsRenderer, sectionRenderer, unavailableRenderer } from "./DisplayItem"
import { freeTextRenderer } from "./FreeTextItem"
import { instrumentRenderer } from "./InstrumentItem"
import { numberRenderer } from "./NumberItem"
import { reasonRenderer } from "./ReasonItem"
import { scaleRenderer } from "./ScaleItem"
import { yesNoRenderer } from "./YesNoItem"
import type { ItemRenderer } from "./types"

const RENDERERS: Record<string, ItemRenderer> = {
  demographics: demographicsRenderer,
  reason: reasonRenderer,
  instrument: instrumentRenderer,
  section: sectionRenderer,
  instructions: instructionsRenderer,
  free_text: freeTextRenderer,
  single_choice: singleChoiceRenderer,
  multi_choice: multiChoiceRenderer,
  yes_no: yesNoRenderer,
  scale: scaleRenderer,
  number: numberRenderer,
  date: dateRenderer,
}

/** The renderer for one item type; the "available soon" one for the rest. */
export function rendererFor(itemType: string): ItemRenderer {
  return RENDERERS[itemType] ?? unavailableRenderer
}

/** The item types this portal can draw. Exported for the tests to walk. */
export const RENDERED_ITEM_TYPES = Object.keys(RENDERERS)
