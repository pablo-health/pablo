# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Readers turn an export archive into typed records.

One module per source system. A reader is pure: it takes a directory and
returns dataclasses; it opens no database and calls no model. Every value it
returns comes from the archive's fixed layout, which is why each reader is
written against a captured fixture of that system's real output and not
against a description of it.
"""

from .simplepractice import SimplePracticeArchive, read_simplepractice_archive

__all__ = ["SimplePracticeArchive", "read_simplepractice_archive"]
