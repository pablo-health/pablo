# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Claims: assembly and validation shared by the superbill, export, and
claim-assembly surfaces, plus filing and eligibility checks through a
practice's own clearinghouse account.

See ``app.claims.clearinghouse`` for the operations this package exposes,
``app.claims.credentials`` for how a practice's API key is resolved, and
``app.claims.stedi`` for the one vendor implementation this codebase ships.
"""
