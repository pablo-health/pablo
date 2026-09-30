# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Former repository names that callers outside this package still import."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app import repositories
from app.services.ical_sync_service import ICalSyncService


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


def test_the_ical_sync_service_still_builds_from_its_original_arguments() -> None:
    """Callers built before outside sessions pass four repositories, not five.

    They must still get a working service, with the default outside-events
    repository, rather than a TypeError on every sync.
    """
    default_repo = MagicMock()
    with patch(
        "app.repositories.get_external_calendar_event_repository", return_value=default_repo
    ) as factory:
        service = ICalSyncService(
            config_repo=MagicMock(),
            appointment_repo=MagicMock(),
            patient_repo=MagicMock(),
            mapping_repo=MagicMock(),
        )
    factory.assert_called_once_with()
    assert service is not None
