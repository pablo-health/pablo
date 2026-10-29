# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Both engine builders keep bound parameters out of exception text.

A failed statement's ``StatementError`` otherwise reads
``[parameters: {...}]`` with the row's values, and that text is what the
traceback logger and the error-reporting formatter copy forward. The
statement is still reported; the values are not.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.db import _build_cloud_sql_engine, _build_direct_engine
from app.settings import Settings

from tests.test_cloud_sql_connector import (
    _inject_connector,
    _make_connector_module,
    _restore_modules,
)


def test_direct_engine_hides_parameters() -> None:
    settings = Settings(database_url="postgresql://dbuser:s3cr3t@localhost/mydb")
    with patch("app.db.create_engine") as create_engine:
        create_engine.return_value = MagicMock()
        _build_direct_engine(settings, [])
    assert create_engine.call_args.kwargs["hide_parameters"] is True


def test_cloud_sql_engine_hides_parameters() -> None:
    settings = Settings(
        database_url="postgresql://dbuser:s3cr3t@localhost/mydb",
        db_use_cloud_sql_connector=True,
        cloud_sql_instance_connection_name="my-project:us-central1:my-instance",
    )
    connector_mod, _connector_cls, _ip_types = _make_connector_module()
    original = _inject_connector(connector_mod)
    try:
        with patch("app.db.create_engine") as create_engine:
            create_engine.return_value = MagicMock()
            _build_cloud_sql_engine(settings, [])
    finally:
        _restore_modules(original)
    assert create_engine.call_args.kwargs["hide_parameters"] is True
