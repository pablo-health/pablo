# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Cloud Run job entrypoint — fan ``alembic upgrade head`` across tenants.

Mirrors ``backend/bin/migrate.py``: chdir into ``backend/`` so alembic.ini's
``prepend_sys_path = .`` resolves correctly, then run the fan-out CLI.

The deploy-time ``pablo-migrate`` job upgrades the ``practice`` template
schema and platform tables; this job upgrades every per-tenant schema.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent.parent)
    sys.path.insert(0, str(Path.cwd()))

    from app.db import get_engine
    from app.db.migrate_tenants import (
        aggregate_exit_code,
        fan_out,
        list_active_tenant_schemas,
        summarize,
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("pablo.migrate_tenants")

    engine = get_engine()
    schemas = list_active_tenant_schemas(engine)
    logger.info("found %d active tenant schema(s) to migrate", len(schemas))
    if not schemas:
        sys.exit(0)

    results = fan_out(engine, schemas)
    logger.info("fan-out complete: %s", summarize(results))
    sys.exit(aggregate_exit_code(results))
