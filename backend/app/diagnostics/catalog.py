# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Loader for the bundled ICD-10-CM catalog.

Reads the tab-separated reference snapshot in :mod:`app.diagnostics.data` and
upserts it into the platform ``icd10_codes`` table. The data is public-domain
ICD-10-CM (NCHS/CMS); the file ships with the engine so the catalog is
populated at deploy-time bootstrap with no network access. Adding or refreshing
codes is a data change — re-generate the file and redeploy.

The loader is idempotent (upsert by code) and reusable: point it at any file in
the same four-column format (``code``, ``billable``, ``category``,
``description``; ``#`` comment lines and the header row are ignored).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ..db.platform_models import Icd10CodeRow

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

DATA_DIR = Path(__file__).resolve().parent / "data"
# The bundled snapshot. Bump the filename when adopting a newer fiscal-year
# release (the loader takes the path explicitly, so callers stay in control).
BUNDLED_CATALOG = DATA_DIR / "icd10cm_2026.tsv"


def _read_rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line or line.startswith("#") or line.startswith("code\t"):
                continue
            code, billable, category, description = line.split("\t")
            yield {
                "code": code,
                "billable": billable,
                "category": category,
                "description": description,
            }


def load_icd10_catalog(session: Session, path: Path = BUNDLED_CATALOG) -> int:
    """Upsert the ICD-10-CM codes in ``path`` into ``icd10_codes``. Idempotent.

    Returns the number of codes processed. Does not commit — the caller owns the
    transaction (the seed flushes alongside the diagnostic definitions).
    """
    count = 0
    for row in _read_rows(path):
        billable = row["billable"] == "1"
        category = row["category"] or None
        existing = session.get(Icd10CodeRow, row["code"])
        if existing is None:
            session.add(
                Icd10CodeRow(
                    code=row["code"],
                    description=row["description"],
                    billable=billable,
                    category=category,
                )
            )
        else:
            existing.description = row["description"]
            existing.billable = billable
            existing.category = category
        count += 1
    session.flush()
    return count


@lru_cache(maxsize=1)
def known_icd10_codes() -> frozenset[str]:
    """The set of ICD-10-CM codes in the bundled reference file.

    File-based (not a DB query) so any caller can validate a code without a
    session — e.g. request-model validators that run before a transaction
    exists. Cached: the bundled file only changes on deploy.
    """
    return frozenset(row["code"] for row in _read_rows(BUNDLED_CATALOG))
