# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The video services an appointment can be held on.

Each module here implements ``app.services.telehealth.MeetingProvider`` for
one vendor, and keeps everything vendor-shaped — API paths, URL parameters,
OAuth scopes, response JSON — inside itself. The layer above deals in
appointments and meeting links.

Import the submodules directly (``.manual``, ``.doxy``, ``.meet``, ``.zoom``,
``.registry``); this package intentionally re-exports nothing, because the
registry imports the implementations and the implementations import the
vocabulary the seam defines.

Pablo captures no media from any of them. A session's audio is recorded on
the clinician's own machine, inside Pablo's boundary, identically whichever
room the session was held in.
"""
