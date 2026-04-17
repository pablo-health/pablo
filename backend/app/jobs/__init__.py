# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Scheduled jobs run out-of-band by Cloud Run Jobs + Cloud Scheduler.

Each module is invokable as a script (``python -m app.jobs.<name>``) and
exits with a non-zero status on failure so Cloud Scheduler can surface
it as an incident.
"""
