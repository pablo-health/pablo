# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The one place a practice schema's row-level security is set at run time.

Every caller that sets row-level security on a live practice goes through
:func:`apply_practice_rls`: provisioning, the pass after every migrate, an
archive restore, and the single-practice conversion. It makes the schema match
the row-level security the tenant templates carry (``rls_apply``), and decides
nothing itself.

**Which templates.** The engine's ``tenant_template.sql``, then each template
``TENANT_RLS_EXTRA_TEMPLATES`` names, in order. A deployment that adds its own
practice-schema tables ships a template of the same shape for them (schema,
marker, row-level security) and names it there. Read once per process: the
files are part of the deployed build and do not change under it.

**Tables no template creates.** ``strict`` decides. Provisioning is strict: a
schema it just built holds only what templates create, so a table outside them
means a template is missing from the setting, and the practice is refused
rather than handed out with a table that has no row-level security at all.
The other callers are lenient: a live schema can hold tables put there some
other way, and those are left as they are and named in a warning.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ..settings import get_settings
from .rls_apply import apply_tenant_rls, desired_from_sql
from .rls_state import read_live_rls

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from . import RlsReconcileCounts
    from .rls_desired import DesiredTable

_ENGINE_TEMPLATE = Path(__file__).resolve().parent / "tenant_template.sql"


def tenant_template_paths() -> tuple[Path, ...]:
    """The engine's template, then every template the deployment names."""
    extra = get_settings().tenant_rls_extra_templates
    return (_ENGINE_TEMPLATE, *(Path(p) for p in extra))


@lru_cache(maxsize=4)
def _desired_for(paths: tuple[Path, ...]) -> dict[str, DesiredTable]:
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        msg = f"TENANT_RLS_EXTRA_TEMPLATES names templates that do not exist: {missing}"
        raise RuntimeError(msg)
    return desired_from_sql(*(p.read_text() for p in paths))


def desired_rls() -> dict[str, DesiredTable]:
    """Every practice-schema table's row-level security, as the templates say it."""
    return _desired_for(tenant_template_paths())


def apply_practice_rls(session: Session, schema_name: str, *, strict: bool) -> RlsReconcileCounts:
    """Make ``schema_name``'s row-level security match the templates.

    Leaves the session's search path alone: the applier runs every statement
    under an empty path, and the templates name every object in full, so
    nothing resolves through whatever the caller had set. Does not commit:
    commit once per schema, which releases the tables the pass changed.

    With ``strict``, raises before changing anything if the schema holds a
    table no template creates.
    """
    from . import _validate_schema_name  # the package imports this module

    _validate_schema_name(schema_name)
    desired = desired_rls()
    if strict:
        unknown = sorted(set(read_live_rls(session, schema_name).flags) - set(desired))
        if unknown:
            msg = (
                f"{schema_name} holds tables no tenant template creates, so nothing would "
                f"give them row-level security: {', '.join(unknown)}. A template is missing "
                "from TENANT_RLS_EXTRA_TEMPLATES."
            )
            raise RuntimeError(msg)
    return apply_tenant_rls(session, schema_name, desired)
