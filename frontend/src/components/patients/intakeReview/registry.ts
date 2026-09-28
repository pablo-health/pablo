// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Which read-only view draws which kind of question on the chart.
 *
 * Keyed by `item_type`, like the portal's renderer registry beside it, and
 * meant to cover the same set: every question a patient can be asked is one
 * a clinician can read back in the shape it was asked. A test walks the
 * portal's list and fails on a type with no view here, so a new item type
 * cannot reach patients while the chart still prints it as raw values.
 *
 * A type neither registry knows reads through the fallback, which spells the
 * stored answer out rather than hiding it.
 */

import { multiChoiceView, scaleView, singleChoiceView, yesNoView } from "./ChoiceViews"
import { consentDocumentView } from "./ConsentDocumentView"
import {
  dateView,
  demographicsView,
  emergencyContactView,
  fallbackView,
  freeTextView,
  instructionsView,
  numberView,
  reasonView,
  sectionView,
} from "./EntryViews"
import { documentRequestView, insuranceCardView } from "./FileViews"
import { instrumentView } from "./InstrumentView"
import type { ItemView } from "./types"

const VIEWS: Record<string, ItemView> = {
  demographics: demographicsView,
  reason: reasonView,
  instrument: instrumentView,
  section: sectionView,
  instructions: instructionsView,
  free_text: freeTextView,
  single_choice: singleChoiceView,
  multi_choice: multiChoiceView,
  yes_no: yesNoView,
  scale: scaleView,
  number: numberView,
  date: dateView,
  emergency_contact: emergencyContactView,
  consent_document: consentDocumentView,
  insurance_card: insuranceCardView,
  document_request: documentRequestView,
}

/** The view for one item type; the fallback for the rest. */
export function viewFor(itemType: string): ItemView {
  return VIEWS[itemType] ?? fallbackView
}

/** The item types the chart can draw. Exported for the tests to walk. */
export const VIEWED_ITEM_TYPES = Object.keys(VIEWS)
