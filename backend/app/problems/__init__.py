# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A client's problem list: the record of their diagnoses.

One row per problem — a label, an optional ICD-10-CM code, and a status of
active, rule-out or resolved — ordered so the first active problem is the
primary diagnosis. ``patients.diagnosis`` is a display line derived from the
active problems; nothing writes it directly.
"""
