# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The tenant template's row-level security section, checked without a database.

``tenant_template.sql`` ends with the row-level security a practice schema
gets, after a marker line provisioning splits on. These pin the parts of that
arrangement a database is not needed to see: the regen script and provisioning
spell the marker the same way, the committed section names no schema of its
own, every policy in it is fingerprinted, and the capture's extraction keeps
what it should and nothing else. Whether the section equals what
``enable_rls_on_schema`` produces is
``tests_integration/database/test_rls_template_parity.py``; whether it is
current is the ``Tenant template matches alembic head`` CI job.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from app.db.provisioning import (
    _TENANT_TEMPLATE_SQL_PATH,
    TEMPLATE_ROW_SECURITY_MARKER,
)
from app.db.rls_state import FINGERPRINT_PREFIX

if TYPE_CHECKING:
    from types import ModuleType

_BACKEND = Path(__file__).resolve().parents[1]
_REGEN = _BACKEND / "scripts" / "regen_tenant_template.py"
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tenant_template"


@pytest.fixture(scope="module")
def regen() -> ModuleType:
    spec = importlib.util.spec_from_file_location("regen_tenant_template", _REGEN)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sections() -> tuple[str, str]:
    body, marker, row_security = _TENANT_TEMPLATE_SQL_PATH.read_text().partition(
        TEMPLATE_ROW_SECURITY_MARKER
    )
    assert marker, "tenant_template.sql has no row-level security section; regenerate it"
    return body, row_security


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text()


def test_the_regen_script_and_provisioning_agree_on_the_marker(regen: ModuleType) -> None:
    assert regen.ROW_SECURITY_MARKER == TEMPLATE_ROW_SECURITY_MARKER
    assert regen.FINGERPRINT_PREFIX == FINGERPRINT_PREFIX


def test_the_row_security_section_names_no_schema_of_its_own(regen: ModuleType) -> None:
    """Every practice-schema reference is the placeholder, never a real name."""
    _, row_security = _sections()
    assert regen.CAPTURE_SCHEMA not in row_security
    assert re.findall(r"(?<![A-Za-z0-9_])practice[a-z0-9_]*\.", row_security) == []


def test_every_policy_in_the_section_is_forced_and_fingerprinted() -> None:
    _, row_security = _sections()
    created = set(re.findall(r"^CREATE POLICY (\w+) ON (\S+) ", row_security, re.MULTILINE))
    commented = set(
        re.findall(
            rf"^COMMENT ON POLICY (\w+) ON (\S+) IS '{FINGERPRINT_PREFIX}[0-9a-f]{{64}}';$",
            row_security,
            re.MULTILINE,
        )
    )
    assert created, "the section carries no policies"
    assert created <= commented
    forced = set(
        re.findall(
            r"^ALTER TABLE ONLY (\S+) FORCE ROW LEVEL SECURITY;$", row_security, re.MULTILINE
        )
    )
    enabled = set(
        re.findall(r"^ALTER TABLE (\S+) ENABLE ROW LEVEL SECURITY;$", row_security, re.MULTILINE)
    )
    assert forced == enabled
    assert {table for _, table in created} <= forced


def test_the_schema_part_forces_nothing() -> None:
    """The template schema itself is built from the schema part, and must stay unforced."""
    body, _ = _sections()
    assert "ROW LEVEL SECURITY" not in body


def test_the_extraction_keeps_flags_and_fingerprinted_policies(regen: ModuleType) -> None:
    section = regen._row_security_section(
        _fixture("row_security_capture.sql"), regen.CAPTURE_SCHEMA, _fixture("schema_part.sql")
    )

    assert "ALTER TABLE ONLY __TENANT_SCHEMA__.notes FORCE ROW LEVEL SECURITY;" in section
    assert "ALTER TABLE __TENANT_SCHEMA__.notes ENABLE ROW LEVEL SECURITY;" in section
    assert "CREATE POLICY rls_note_access ON __TENANT_SCHEMA__.notes USING" in section
    assert "__TENANT_SCHEMA__.has_patient_access(patient_id" in section
    assert (
        "COMMENT ON POLICY rls_note_access ON __TENANT_SCHEMA__.notes "
        "IS 'rls-fingerprint-v1-aaaa';" in section
    )
    # Not the reconcile's (no fingerprint): its own pass re-applies it.
    assert "rls_practice_directory_read" not in section
    # Already created, identically, by the schema part: fingerprint only.
    assert "CREATE POLICY rls_audit_owner_client_read" not in section
    assert "DROP POLICY" not in section
    assert "COMMENT ON POLICY rls_audit_owner_client_read" in section
    assert regen.CAPTURE_SCHEMA not in section


def test_a_policy_the_schema_part_has_differently_is_replaced(regen: ModuleType) -> None:
    schema_part = _fixture("schema_part.sql").replace("patient_id IS NOT NULL", "true")
    section = regen._row_security_section(
        _fixture("row_security_capture.sql"), regen.CAPTURE_SCHEMA, schema_part
    )
    drop = "DROP POLICY rls_audit_owner_client_read ON __TENANT_SCHEMA__.audit_logs;"
    create = "CREATE POLICY rls_audit_owner_client_read ON __TENANT_SCHEMA__.audit_logs"
    assert section.index(drop) < section.index(create)


def test_the_extraction_refuses_a_section_that_still_names_the_capture(
    regen: ModuleType,
) -> None:
    leaky = _fixture("row_security_capture.sql").replace(
        "OR (restricted AND",
        "OR ('practice_template_capture' IS NOT NULL AND restricted AND",
    )
    with pytest.raises(SystemExit, match="still names"):
        regen._row_security_section(leaky, regen.CAPTURE_SCHEMA, _fixture("schema_part.sql"))
