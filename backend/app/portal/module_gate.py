# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A portal module a practice has turned off answers 404 to its clients.

The deployment decides which modules are mounted at all (``PORTAL_MODULES``;
see ``app.main.portal_module_routers``). A practice may then turn some of
those off for its own clients. This is the enforcement half of that choice:
a dependency on every patient-facing route of a module, so a client of a
practice without it gets the same 404 an unmounted module gives — whatever
the shell does or does not draw.

Clinician-facing routes carry no such gate. A practice reading what its
clients already sent is not a portal module.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, status

from ..auth.patient_context import PatientContext, get_patient_context
from .portal_settings import portal_settings_for_schema, practice_offers_module

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def _not_found() -> HTTPException:
    """The same 404 a route that is not mounted gives."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


def require_portal_module(name: str) -> Callable[[PatientContext], None]:
    """A dependency refusing *name*'s routes to clients of a practice without it."""

    def _practice_offers_it(
        patient: Annotated[PatientContext, Depends(get_patient_context)],
    ) -> None:
        try:
            settings = portal_settings_for_schema(patient.practice_schema)
        except Exception as exc:
            logger.warning(
                "Portal settings lookup failed; refusing the module",
                extra={"portal_module": name, "error_type": type(exc).__name__},
            )
            raise _not_found() from None
        if not practice_offers_module(settings, name):
            raise _not_found()

    return _practice_offers_it
