# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Former repository names that callers outside this package still import."""

from __future__ import annotations

from app import repositories


def test_the_ical_mapping_factory_name_still_resolves_to_the_source_mapping_one() -> None:
    """Code built on this package imports the old name at load time.

    Removing it breaks that code on startup, so it stays until those callers
    have moved to ``get_patient_source_mapping_repository``.
    """
    assert (
        repositories.get_ical_client_mapping_repository
        is repositories.get_patient_source_mapping_repository
    )
    assert "get_ical_client_mapping_repository" in repositories.__all__
