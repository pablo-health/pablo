// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Which renderer draws which kind of question.
 *
 * Keyed by `item_type`, the same vocabulary `backend/app/intake/items.py`
 * constrains. A type with no entry here renders as a step still to come, so
 * a practice that puts a question on a form this portal cannot ask yet gets
 * a patient who is told so rather than a blank screen.
 *
 * **Which types are missing is not an oversight.** A guardian is a standard
 * block of fields whose screen has not been built, and a form carrying one
 * tells the patient so rather than showing a blank screen.
 *
 * What is here divides into four, and the difference is where the question
 * comes from and who writes its answer. Demographics, reason and a measure
 * are asked in wording the engine owns and serves, so those renderers read
 * the form response. Most of the rest are questions the practice wrote,
 * carried on the item as `label` and `help_text`, which is why those
 * renderers share one frame. A consent document is the third: the words are
 * a stored document the renderer fetches, and signing is its own route
 * rather than the walk's save. The two file-backed types are the fourth and
 * work the same way — a card and a requested document are answered by
 * uploading, and the server writes the answer from the rows that records.
 * See `writesItself` in `./types`.
 *
 * The server draws the same line from the other side: it refuses an answer
 * to any of those three types sent through the save route, and a form still
 * needing one cannot be handed in. So nothing here can let a patient believe
 * a form is finished when it is not — the "is it finished" question is only
 * ever asked of the server.
 */

import { multiChoiceRenderer, singleChoiceRenderer } from "./ChoiceItem"
import { consentDocumentRenderer } from "./ConsentDocumentItem"
import { dateRenderer } from "./DateItem"
import { demographicsRenderer } from "./DemographicsItem"
import { instructionsRenderer, sectionRenderer, unavailableRenderer } from "./DisplayItem"
import { documentRequestRenderer } from "./DocumentRequestItem"
import { emergencyContactRenderer } from "./EmergencyContactItem"
import { freeTextRenderer } from "./FreeTextItem"
import { instrumentRenderer } from "./InstrumentItem"
import { insuranceCardRenderer } from "./InsuranceCardItem"
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
  emergency_contact: emergencyContactRenderer,
  consent_document: consentDocumentRenderer,
  insurance_card: insuranceCardRenderer,
  document_request: documentRequestRenderer,
}

/** The renderer for one item type; the "available soon" one for the rest. */
export function rendererFor(itemType: string): ItemRenderer {
  return RENDERERS[itemType] ?? unavailableRenderer
}

/** The item types this portal can draw. Exported for the tests to walk. */
export const RENDERED_ITEM_TYPES = Object.keys(RENDERERS)
