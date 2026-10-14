# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A client's history as chart fields: written once, true until it isn't.

Psychiatric, trauma, social, medical and family history, cultural
considerations and the substance-use baseline are recorded on the chart, one
free-text value per field, rather than carried forward in note text. A note
reads them from the chart as written. Every change keeps the value it
replaced, with who wrote it and when, so the chart can show what it said on
any date; removing a value is a change like any other.
"""
