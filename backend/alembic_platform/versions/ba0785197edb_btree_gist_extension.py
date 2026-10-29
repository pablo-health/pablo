# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""btree_gist: the extension the appointments exclusion constraint needs

An ``EXCLUDE USING gist`` constraint that pairs an equality (``user_id WITH
=``) with a range overlap (``tstzrange(...) WITH &&``) needs a GiST operator
class for the scalar column, and Postgres only ships one for scalars in the
``btree_gist`` contrib extension. The tenant revision that adds the constraint
(``48e739bd21f9``) relies on it.

An extension belongs to the database, not to a schema, so it is installed
here — once — rather than in the tenant chain, which is fanned out once per
practice schema. Its objects go in ``public``, named explicitly: this chain
runs with ``platform`` first on the search_path, and without the clause the
extension's hundred-odd support functions would land in the platform schema,
where they are not platform objects and the legacy-shape comparison rightly
rejects them. GiST default operator classes are found by type, not by
search_path, so the tenant constraint resolves them wherever they live. The
platform template is unchanged — ``pg_dump --schema=platform`` sees nothing
in ``public`` — and a fresh install gets the extension from this revision.

``btree_gist`` is a trusted extension (Postgres 13+), so the application role
can install it without superuser as long as it holds ``CREATE`` on the
database, which it does everywhere the chain runs. Cloud SQL lists it among
the supported extensions.

Revision ID: ba0785197edb
Revises: c7d2a9e4f381
Create Date: 2026-10-29
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "ba0785197edb"
down_revision: str | Sequence[str] | None = "c7d2a9e4f381"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist SCHEMA public")


def downgrade() -> None:
    # Left installed: every practice schema's exclusion constraint depends on
    # it, and the tenant chain is what removes those. Dropping the extension
    # here would fail (or cascade through every tenant) on any database where
    # a tenant has not been downgraded first.
    pass
