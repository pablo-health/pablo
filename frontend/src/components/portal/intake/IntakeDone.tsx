// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The done screen.
 *
 * The 201 response carries each screener's total and its severity band, and
 * this screen shows neither. A PHQ-9 total is a number with a clinical
 * meaning attached, and the person qualified to attach it is the clinician
 * reading the chart — not a page a patient meets alone, minutes after
 * answering nine questions about how bad the last fortnight has been.
 */

import { CrisisFooter } from "./CrisisFooter"
import { DONE_BODY, DONE_HEADING } from "./intakeCopy"

export function IntakeDone() {
  return (
    <section data-testid="intake-done" aria-labelledby="intake-done-heading">
      <h2 id="intake-done-heading" className="text-lg font-semibold text-neutral-900">
        {DONE_HEADING}
      </h2>
      <p className="mt-2 text-sm text-neutral-700">{DONE_BODY}</p>
      <CrisisFooter />
    </section>
  )
}
