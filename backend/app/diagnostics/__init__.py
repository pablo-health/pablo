# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Diagnostic-criteria assessment engine.

Records structured diagnostic determinations (criterion counts + gates ->
diagnosis + ICD-10-CM code), distinct from the continuous symptom scores in
``app.outcome_measures``. Definitions are data (platform-schema rows); the
single metadata-driven evaluator is the only logic in code.

See ``docs/architecture/diagnostic-criteria-engine.md``.
"""
