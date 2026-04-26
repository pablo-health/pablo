# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note-type authorization hook.

OSS default is allow-all: self-hosters either don't gate note types at all,
or implement their own subscription policy. Downstream overlays (e.g.
pablo-saas) override :func:`get_note_type_authorizer` via FastAPI's
``app.dependency_overrides`` to enforce tier rules at note-creation time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import User


class NoteTypeAuthorizer:
    """Decides whether a caller may create a note of a given type.

    OSS default is allow-all (self-hosters set their own subscription policy
    or simply don't gate). Downstream overlays (e.g. pablo-saas) override
    this dependency via ``app.dependency_overrides[get_note_type_authorizer]``
    to enforce their tier rules.
    """

    def is_allowed(self, user: User, note_type: str) -> bool:  # noqa: ARG002 — args document the override contract for downstream overlays
        return True


_DEFAULT_AUTHORIZER = NoteTypeAuthorizer()


def get_note_type_authorizer() -> NoteTypeAuthorizer:
    """Return the process-wide default authorizer.

    Overridden by downstream overlays at app bootstrap.
    """
    return _DEFAULT_AUTHORIZER
