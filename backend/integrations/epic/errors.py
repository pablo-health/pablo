# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Exceptions for the Epic / MyChart integration."""


class EpicConfigError(Exception):
    """Raised when required configuration (e.g. client id) is missing."""


class EpicAuthError(Exception):
    """Raised when the SMART on FHIR authorization flow fails."""
