# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The waitlist: existing clients waiting for a better or more regular time.

Each clinician keeps their own list, in their own order (:mod:`.service`,
:mod:`app.routes.waitlist`). An entry says which days and parts of the day
the client wants, in person or by video, and for which session type. Only an
active client can be on it. Nothing here offers a time to anyone or sends
anything; it is the list a practice works from.
"""
