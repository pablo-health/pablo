# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""has_patient_access: resolve the grant table in its own schema

``has_patient_access`` gates the read policy on ``patient_documents``,
``notes``, ``chat_conversations``, ``medications``, ``outcome_measures``,
``diagnostic_assessments``, ``appointments`` and the prescribing tables —
most of the clinical surface. Every tenant schema holds its own copy and
every policy calls its own copy schema-qualified.

The body named ``patient_clinicians`` unqualified, and the function is
``LANGUAGE sql STABLE`` with no ``SET search_path`` and no
``SECURITY DEFINER``. So the grant table was resolved against the
CALLER's ``search_path``, not against the schema the function lives in:
tenant A's function, called with tenant B ahead of A on the path,
answered from B's grants. Measured both ways — a foreign grant said yes,
and A's own grant said no.

Nothing in the tree can produce that state. ``_VALID_SCHEMA_RE`` admits
no comma or space, so the ``SET search_path`` interpolation cannot be
widened to two practice schemas; every ``SET search_path`` in the
codebase is the fixed ``{schema}, platform, public`` form; and there is
no ``SECURITY DEFINER`` function anywhere to borrow rights through. This
is hardening, not a live-escape fix.

It earns its place because the safety argument is entirely negative. It
rests on an exhaustive audit of every call site staying true forever,
across every future reporting query, operator tool and two-schema
migration helper. Binding the lookup replaces "nothing does this yet"
with "this cannot mean that".

WHY NOT ``SET search_path`` ON THE FUNCTION, which is the textbook fix:
it would be actively harmful here. ``scripts/regen_tenant_template.py``
rewrites only dot-qualified ``practice.`` occurrences into
``__TENANT_SCHEMA__`` (the substitution is
``re.sub(r"(?<![A-Za-z0-9_])practice\\.", ...)``). A pinned
``SET search_path TO practice, pg_catalog`` carries no trailing dot, so
it would pass through the regeneration verbatim and every
freshly-provisioned tenant would get a function pointed at the
``practice`` schema — converting a latent hazard into a live
cross-tenant leak on the whole clinical surface. Qualifying the table
reference is what the regeneration understands.

The signature, volatility and semantics are unchanged: same
``(uuid, character varying)`` overload, same ``STABLE``, same expiry
rule. Only the name resolution is pinned, so every existing policy that
calls it keeps working without being touched.

Revision ID: f6d0a3e94b21
Revises: e5c9f2d73a18
Create Date: 2026-08-31
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f6d0a3e94b21"
down_revision: str | Sequence[str] | None = "e5c9f2d73a18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _current_schema() -> str:
    return op.get_bind().execute(text("SELECT current_schema()")).scalar_one()


_TEMPLATE = """
    CREATE OR REPLACE FUNCTION has_patient_access(
        p_patient_id UUID,
        p_user_id    VARCHAR
    ) RETURNS BOOLEAN
    LANGUAGE sql
    STABLE
    AS $$
        SELECT EXISTS (
            SELECT 1 FROM {grant_table}
            WHERE patient_id = p_patient_id
              AND user_id::text = p_user_id
              AND (expires_at IS NULL OR expires_at > now())
        );
    $$
"""


def _body(grant_table: str) -> str:
    # Migration DDL. ``grant_table`` is built from ``current_schema()`` as
    # reported by the connection alembic is already running against — not
    # from user input, and not reachable from a request.
    return _TEMPLATE.format(grant_table=grant_table)


def upgrade() -> None:
    op.execute(_body(f"{_current_schema()}.patient_clinicians"))


def downgrade() -> None:
    op.execute(_body("patient_clinicians"))
