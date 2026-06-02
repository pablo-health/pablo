# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Outcome measures — scored clinical instruments (PHQ-9, GAD-7, generic).

Public surface:

* :mod:`instruments` — instrument registry, validation helpers, scoring.
* :mod:`router` — FastAPI router (mounted in ``main.py``).
* :mod:`schemas` — Pydantic request / response models.
* :mod:`service` — business logic (create, list, get, soft-delete).
"""
