# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""One-off operator commands.

Work an operator runs deliberately, once, after a deploy — as opposed to
``backend/bin/``, which holds the entrypoints a job runs every time it
deploys. The distinction that matters is what a mistake costs: a migration
runs unattended on every practice and has to be right the first time, while
what lives here moves data whose before and after a person may want to look
at.

Every command here is idempotent, because the only safe way to run
something once is to be able to run it twice.
"""
