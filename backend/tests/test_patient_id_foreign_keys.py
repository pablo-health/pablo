# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Every ``patient_id`` column goes with its client, or says why it stays.

Deleting a client's ``patients`` row is how a hard purge removes the client
(``app.jobs.hard_purge_cron``). A table that names the client in a
``patient_id`` column with no foreign key to ``patients`` is not reached by
that delete, and keeps the purged client's rows with nothing left to find
them by. Seven tables did, until revision ``8c5e65a0e56a``.

So, read from the committed tenant template: every table with a column named
``patient_id`` either

* has ``FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE``,
* or cascades from a table that does, through a composite key carrying
  ``patient_id`` (an intake answer off its assignment, a message off its
  thread),
* or is in ``_REVIEWED`` below with the reason it stays.

A new ``patient_id`` column with none of these fails here, by table name.
Whether the template is current with the migrations is the ``Tenant template
matches alembic head`` CI job; that the cascade really empties a purged
client's rows is ``tests_integration/database/test_hard_purge_rls.py``.
"""

from __future__ import annotations

import re

from app.db.provisioning import _TENANT_TEMPLATE_SQL_PATH

#: Tables whose ``patient_id`` deliberately does not cascade, and why. Adding
#: one is a review decision: say what keeps the client's rows from outliving a
#: purge, or why they must.
_REVIEWED: dict[str, str] = {
    "audit_logs": (
        "a forensic record outlives what it names; the identifier is kept as "
        "recorded and the purge writes its own entry"
    ),
    "tasks": (
        "ON DELETE SET NULL: a compliance task linked to a client is the "
        "clinician's licensure or supervision record and is kept, unlinked; "
        "the purge deletes the client's other tasks itself"
    ),
}

_CREATE_TABLE = re.compile(r"^CREATE TABLE __TENANT_SCHEMA__\.(\w+) \((.*?)^\);", re.M | re.S)
_FOREIGN_KEY = re.compile(
    r"^ALTER TABLE ONLY __TENANT_SCHEMA__\.(\w+)\s+ADD CONSTRAINT \w+ "
    r"FOREIGN KEY \(([^)]*)\) REFERENCES __TENANT_SCHEMA__\.(\w+)\(([^)]*)\)([^;]*);",
    re.M,
)


def _columns(body: str) -> set[str]:
    names = set()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("CONSTRAINT"):
            names.add(stripped.split()[0])
    return names


def _split(cols: str) -> list[str]:
    return [c.strip() for c in cols.split(",")]


def _patient_id_tables(sql: str) -> set[str]:
    return {m.group(1) for m in _CREATE_TABLE.finditer(sql) if "patient_id" in _columns(m.group(2))}


def _cascading_tables(sql: str) -> set[str]:
    """Tables whose rows go when their client's ``patients`` row is deleted.

    Direct: ``(patient_id) -> patients(id) ON DELETE CASCADE``. Transitive:
    a cascading key whose ``patient_id`` column maps to ``patient_id`` on a
    table already known to cascade.
    """
    edges: list[tuple[str, list[str], str, list[str]]] = []
    for m in _FOREIGN_KEY.finditer(sql):
        if "ON DELETE CASCADE" not in m.group(5):
            continue
        edges.append((m.group(1), _split(m.group(2)), m.group(3), _split(m.group(4))))
    covered = {
        table
        for table, cols, ref, ref_cols in edges
        if ref == "patients" and cols == ["patient_id"] and ref_cols == ["id"]
    }
    changed = True
    while changed:
        changed = False
        for table, cols, ref, ref_cols in edges:
            if table in covered or ref not in covered or "patient_id" not in cols:
                continue
            if ref_cols[cols.index("patient_id")] == "patient_id":
                covered.add(table)
                changed = True
    return covered


def uncovered_patient_id_tables(sql: str, reviewed: dict[str, str]) -> list[str]:
    """Tables with a ``patient_id`` that neither cascades nor is reviewed."""
    return sorted(_patient_id_tables(sql) - _cascading_tables(sql) - reviewed.keys())


def _template() -> str:
    return _TENANT_TEMPLATE_SQL_PATH.read_text()


def test_every_patient_id_column_cascades_from_patients_or_is_reviewed() -> None:
    missing = uncovered_patient_id_tables(_template(), _REVIEWED)
    assert not missing, (
        f"{missing} name a client in patient_id with no ON DELETE CASCADE from "
        "patients(id), so a hard purge leaves their rows behind. Add "
        "FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE in "
        "the table's revision, or add it to _REVIEWED with the reason it stays."
    )


def test_every_reviewed_table_still_names_a_client() -> None:
    """A reviewed entry for a table that no longer has the column is stale."""
    stale = sorted(_REVIEWED.keys() - _patient_id_tables(_template()))
    assert not stale, f"{stale} have no patient_id column any more; drop them from _REVIEWED"


def test_reviewed_tables_do_not_cascade() -> None:
    """A reviewed table that cascades needs no review; one less exception."""
    redundant = sorted(_REVIEWED.keys() & _cascading_tables(_template()))
    assert not redundant, f"{redundant} cascade from patients; drop them from _REVIEWED"


def test_tasks_are_unlinked_not_deleted() -> None:
    """The compliance-task rule is a SET NULL key, not merely an absent one."""
    keys = [
        m.group(5)
        for m in _FOREIGN_KEY.finditer(_template())
        if m.group(1) == "tasks" and _split(m.group(2)) == ["patient_id"]
    ]
    assert keys == [" ON DELETE SET NULL"]


def test_the_check_reads_the_composite_cascades() -> None:
    """Intake answers and messages are covered through their parents, not
    skipped: the check sees them as patient_id tables and as cascading."""
    sql = _template()
    for table in ("patient_intake_responses", "patient_messages", "patient_message_attachments"):
        assert table in _patient_id_tables(sql)
        assert table in _cascading_tables(sql)


_MUTANT_TABLE = """
CREATE TABLE __TENANT_SCHEMA__.client_scrapbook (
    id uuid NOT NULL,
    patient_id uuid NOT NULL,
    body text
);
"""


def test_a_new_patient_id_column_with_no_key_fails_by_name() -> None:
    mutant = _template() + _MUTANT_TABLE
    assert uncovered_patient_id_tables(mutant, _REVIEWED) == ["client_scrapbook"]


def test_a_new_patient_id_column_passes_once_reviewed() -> None:
    mutant = _template() + _MUTANT_TABLE
    reviewed = {**_REVIEWED, "client_scrapbook": "kept on purpose"}
    assert uncovered_patient_id_tables(mutant, reviewed) == []


def test_a_new_patient_id_column_passes_with_a_cascading_key() -> None:
    mutant = (
        _template() + _MUTANT_TABLE + "\nALTER TABLE ONLY __TENANT_SCHEMA__.client_scrapbook\n"
        "    ADD CONSTRAINT client_scrapbook_patient_id_fkey FOREIGN KEY (patient_id) "
        "REFERENCES __TENANT_SCHEMA__.patients(id) ON DELETE CASCADE;\n"
    )
    assert uncovered_patient_id_tables(mutant, _REVIEWED) == []


def test_a_key_that_does_not_cascade_does_not_count() -> None:
    mutant = (
        _template() + _MUTANT_TABLE + "\nALTER TABLE ONLY __TENANT_SCHEMA__.client_scrapbook\n"
        "    ADD CONSTRAINT client_scrapbook_patient_id_fkey FOREIGN KEY (patient_id) "
        "REFERENCES __TENANT_SCHEMA__.patients(id) ON DELETE RESTRICT;\n"
    )
    assert uncovered_patient_id_tables(mutant, _REVIEWED) == ["client_scrapbook"]
