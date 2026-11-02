# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Guardrail: a Session switches practice through ``set_tenant_schema``, not a raw SET.

``set_tenant_schema`` records the schema on the Session, and an ``after_begin``
listener puts it back on every new transaction whose connection lost it (see
``_reapply_tenant_schema_on_txn_begin``). A raw ``SET search_path`` on a Session
that was set to practice A changes the connection but not that record, so after
the next commit the listener puts A back: the rest of the work runs on the
wrong practice. That is a cross-practice read or write with no error anywhere.

The places below issue a raw SET on purpose — on a fresh Session that never
went through ``set_tenant_schema``, on a bare Connection, or inside the db
package itself. Any new one has to be checked against that rule before it is
added here.
"""

from __future__ import annotations

import re
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent / "app"

_RAW_SET = re.compile(
    r"""(?:execute|exec_driver_sql)\(\s*(?:text\(\s*)?f?["']SET search_path""", re.S
)

#: Reviewed raw ``SET search_path`` statements, per file.
_REVIEWED = {
    "bin/adopt_intake_submissions.py": 1,
    "db/__init__.py": 5,
    "db/intake_seed.py": 1,
    "db/migrate_tenants.py": 2,
    "db/provisioning.py": 4,
    "db/single_practice_migration.py": 1,
    "jobs/audit_retention_cron.py": 2,
    "jobs/hard_purge_cron.py": 6,
    "jobs/hipaa_attestation.py": 1,
    "payments/reconcile.py": 1,
}


def _raw_sets() -> dict[str, int]:
    found = {}
    for path in sorted(_APP.rglob("*.py")):
        count = len(_RAW_SET.findall(path.read_text()))
        if count:
            found[path.relative_to(_APP).as_posix()] = count
    return found


def test_no_new_raw_search_path_switch() -> None:
    found = _raw_sets()
    unreviewed = {name: count for name, count in found.items() if count > _REVIEWED.get(name, 0)}
    assert not unreviewed, (
        "New raw `SET search_path` statements. On a Session, switch practice with "
        "`set_tenant_schema` — a raw SET is undone after the next commit, which "
        "puts the work back on the previous practice. If this one runs on a fresh "
        "Session or a bare Connection, add it to _REVIEWED with that reason in "
        f"review.\n{unreviewed}"
    )


def test_the_reviewed_list_has_no_stale_entries() -> None:
    found = _raw_sets()
    stale = {name: count for name, count in _REVIEWED.items() if found.get(name, 0) < count}
    assert not stale, f"Lower these counts in _REVIEWED to what the files now hold: {stale}"
