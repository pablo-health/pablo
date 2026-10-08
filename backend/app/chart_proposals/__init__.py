# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chart updates a note proposes, decided by the clinician at sign.

The note is the event and the chart is the state. After a draft, a second,
smaller model call compares the chart with what was said in the visit and
proposes an update for each chart field that changed: the field's full
revised text with the existing text kept, one line saying what changed, and
the transcript segments that say so. A proposal citing no segment of this
visit's transcript is dropped before anyone sees it. Nothing changed means
no proposals and nothing to review.

Proposals are stored beside the note, never in its content. The clinician
accepts, edits or discards each one; only an accept or an edit writes the
chart, through the chart's own record, with the note as its source. A
signed note keeps the chart it was drafted against.

A note also proposes, as written, each history field it states that the
chart has no value for. That is how an intake fills the chart.
"""
