# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What the applier's two entry points refuse before they touch a database."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.db.rls_apply import apply_tenant_rls, tenant_rls_differences

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize("entry_point", [apply_tenant_rls, tenant_rls_differences])
def test_a_schema_name_that_is_not_a_plain_identifier_is_refused_before_any_query(
    entry_point: Callable[..., object],
) -> None:
    """The name goes into DDL as text. Nothing is asked of the session first."""

    class NoSession:
        def __getattr__(self, name: str) -> object:
            msg = f"the session was used ({name}) before the schema name was checked"
            raise AssertionError(msg)

    with pytest.raises(ValueError, match="Invalid schema name"):
        entry_point(NoSession(), 'practice"; DROP SCHEMA practice CASCADE; --', {})
